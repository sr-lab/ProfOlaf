import os
import sys
import time
import re
import bibtexparser
import requests
import hashlib

from abc import ABC, abstractmethod
from urllib.parse import urlencode
from scholarly import scholarly
from bibtexparser.bwriter import BibTexWriter
from bibtexparser.bibdatabase import BibDatabase
from enum import Enum

from ..db_management import ArticleData, SelectionStage

DBLP_SEARCH_TAG = "[DBLP]"
SS_SEARCH_TAG = "[Semantic Scholar]"
GS_SEARCH_TAG = "[Google Scholar]"

SS_API_BASE = "https://api.semanticscholar.org/graph/v1"
SS_MAX_RETRIES = 12
_ss_last_request_time = 0.0


def _get_semantic_scholar_api_key() -> str:
    return os.getenv("S2_API_KEY") or os.getenv("SEMANTIC_SCHOLAR_API_KEY") or ""


def _semantic_scholar_min_interval() -> float:
    """Minimum seconds between Semantic Scholar requests (1 RPS with API key)."""
    return 1.0 if _get_semantic_scholar_api_key() else 2.5


def _semantic_scholar_headers() -> dict:
    headers = {}
    api_key = _get_semantic_scholar_api_key()
    if api_key:
        headers["x-api-key"] = api_key
    return headers


def _semantic_scholar_get(url: str, timeout: int = 60) -> requests.Response:
    """GET with rate pacing and automatic retry on HTTP 429."""
    global _ss_last_request_time

    min_interval = _semantic_scholar_min_interval()
    headers = _semantic_scholar_headers()
    backoff = 1

    for attempt in range(SS_MAX_RETRIES + 1):
        elapsed = time.time() - _ss_last_request_time
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)

        response = requests.get(url, headers=headers, timeout=timeout)
        _ss_last_request_time = time.time()

        if response.status_code != 429:
            return response

        if attempt >= SS_MAX_RETRIES:
            print(f"{SS_SEARCH_TAG} Rate limit exceeded after {SS_MAX_RETRIES} retries")
            return response

        retry_after = response.headers.get("Retry-After")
        wait = int(retry_after) if retry_after else backoff
        print(f"{SS_SEARCH_TAG} Rate limit (429), waiting {wait}s (retry {attempt + 1}/{SS_MAX_RETRIES})...")
        time.sleep(wait)
        backoff = min(backoff * 2, 120)

    return response


def _semantic_scholar_search_url(query: str, fields: str | None = None) -> str:
    params = {"query": query}
    if fields:
        params["fields"] = fields
    return f"{SS_API_BASE}/paper/search/match?{urlencode(params)}"

class SearchMethod(Enum):
    GOOGLE_SCHOLAR = "google_scholar"
    SEMANTIC_SCHOLAR = "semantic_scholar"
    DBLP_SEARCH = "dblp"
    OPENALEX = "openalex"
    CROSSREF = "crossref"

    def get_search_class(self):
        """Get the corresponding search method class for this enum value."""
        if self == SearchMethod.GOOGLE_SCHOLAR:
            return GoogleScholarSearchMethod
        elif self == SearchMethod.SEMANTIC_SCHOLAR:
            return SemanticScholarSearchMethod
        elif self == SearchMethod.DBLP_SEARCH:
            return DBLPSearchMethod
        elif self == SearchMethod.OPENALEX:
            return OpenAlexSearchMethod
        elif self == SearchMethod.CROSSREF:
            return CrossrefSearchMethod
        else:
            raise ValueError(f"Unknown search method: {self}")
    
    def create_instance(self):
        """Create an instance of the corresponding search method class."""
        return self.get_search_class()()



# Venues that mark a non-peer-reviewed / placeholder entry. Matched as whole words
# so "arXiv e-prints" / "CoRR abs/..." are rejected while e.g. a venue merely
# containing the letters "corr" is not.
_INVALID_VENUE_PATTERNS = (r"\barxiv\b", r"\bcorr\b", r"\bno title\b")


def check_valid_venue(venue: str) -> bool:
    if not venue:
        return False
    venue_lower = venue.lower()
    return not any(re.search(pattern, venue_lower) for pattern in _INVALID_VENUE_PATTERNS)


def normalize_doi(doi: str) -> str:
    """Normalize a DOI to a bare, lowercased form (no URL/`doi:` prefix) so DOIs
    from different providers compare equal (used as a reliable identity key)."""
    if not doi:
        return ""
    value = str(doi).strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "doi:"):
        if value.startswith(prefix):
            value = value[len(prefix):]
            break
    return value.strip()

#Strategy Pattern
class ArticleSearchMethod(ABC):
    @abstractmethod
    def search(self, query: str):
        pass
    @abstractmethod
    def get_bibtex(self, pub):
        pass
    
    def map_to_pub(self, article: dict):
        pass
    # Optional methods that some strategies may implement
    def get_all_versions_bibtexes(self, pub):
        """Optional method - raises NotImplementedError by default"""
        raise NotImplementedError(f"{self.__class__.__name__} does not support get_all_versions_bibtexes")
    
    def get_snowballing_articles(self, citedby: str, **kwargs):
        """Optional method - raises NotImplementedError by default"""
        raise NotImplementedError(f"{self.__class__.__name__} does not support search_citedby")

    def get_article_data(self, pub, pub_id, iteration: int = 0, selected: SelectionStage = SelectionStage.NOT_SELECTED, new_pub: bool = False, search_method: str = ""):
        """Optional method - raises NotImplementedError by default"""
        raise NotImplementedError(f"{self.__class__.__name__} does not support get_article_data")

class GoogleScholarSearchMethod(ArticleSearchMethod):
    name = SearchMethod.GOOGLE_SCHOLAR.value

    def get_article_data(self, pub, pub_id, iteration: int = 0, selected: SelectionStage = SelectionStage.NOT_SELECTED, new_pub: bool = False, search_method: str = ""):
            """
            Get the article data from the pub.
            """
            pub_info = {}
            pub_info["id"] = pub_id
            pub_info["container_type"] = pub.get("container_type", "")
            pub_info["eprint_url"] = pub.get("pub_url", "") if "eprint_url" not in pub else pub["eprint_url"]
            pub_info["source"] = pub.get("source", "")
            pub_info["title"] = pub.get("bib", {}).get("title", "")
            pub_info["authors"] = pub.get("bib", {}).get("author", "")
            pub_info["venue"] = pub.get("bib", {}).get("venue", "")
            pub_info["pub_year"] = "0" if not pub.get("bib", {}).get("pub_year", "").isdigit() else pub.get("bib", {}).get("pub_year", "")
            pub_info["pub_url"] = pub.get("pub_url", "")
            pub_info["num_citations"] = pub.get("num_citations", 0)
            pub_info["citedby_url"] = pub.get("citedby_url", "")
            pub_info["url_related_articles"] = pub.get("url_related_articles", "")
            pub_info["new_pub"] = new_pub
            pub_info["selected"] = selected
            pub_info["iteration"] = iteration
            pub_info["search_method"] = search_method
            return ArticleData(**pub_info)


    def search(self, query: str):
        print(f"{GS_SEARCH_TAG} Searching for", query)
        pub = scholarly.search_single_pub(query)
        print(f"{GS_SEARCH_TAG} Found", pub)
        if pub is None:
            print(f"{GS_SEARCH_TAG} No results found for", query)
            return None
        scholar_id = pub.get('citedby_url')
        id = hashlib.md5(query.encode('utf-8')).hexdigest() if scholar_id is None else 0
        if id == 0:
            match = re.search(r"cites=(\d+)", scholar_id)
            if match is None:
                print(f"{GS_SEARCH_TAG} No match found for", query)
                return None
            id = int(match.group(1))
        return self.get_article_data(pub, id, new_pub=True, selected=SelectionStage.NOT_SELECTED, search_method=self.name)

    def get_snowballing_articles(self, citedby: str, **kwargs):
        if not str(citedby).isdigit():
            return []
        iteration = kwargs.get("iteration", 0)
        forwards = kwargs.get("forwards", False)
        backwards = kwargs.get("backwards", True)

        if forwards:
            print(f"{GS_SEARCH_TAG} Forward search not supported")
        if not backwards:
            return []

        current_wait_time = 30
        while True:
            try:
                pubs = scholarly.search_citedby(int(citedby))
                break
            except Exception as e:
                print(e)
                print(f"{GS_SEARCH_TAG} Retrying {citedby}, waiting {current_wait_time}...", file=sys.stderr)
                sys.stdout.flush()
                time.sleep(current_wait_time)
                current_wait_time *= 2
                continue
        if pubs.total_results == 0:
            print(f"{GS_SEARCH_TAG} No results found for", citedby)
            return []
        
        articles = []
        for pub in pubs:
            if "citedby_url" not in pub:
                pub_id = hashlib.sha256(pub["bib"]["title"].encode()).hexdigest()
            else:
                m = re.search("cites=[\d+,]*", pub["citedby_url"])
                pub_id = m.group()[6:]
            
            article = self.get_article_data(pub, pub_id, iteration, new_pub=True, search_method=self.name)
            articles.append((article, "citation"))
        return articles

    def get_bibtex(self, pub):
        bibtex = scholarly.bibtex(pub)
        if bibtex is None:
            print(f"{GS_SEARCH_TAG} No bibtex found for", pub.get("title", "Unknown"))
            return None
        return bibtex
    
    def get_all_versions_bibtexes(self, pub):
        versions = scholarly.get_all_versions_bibtexes(pub)
        if versions is None or versions == []:
            #print(f"{GS_SEARCH_TAG} No versions found")
            return []
        for version in versions:
            version = bibtexparser.loads(version)
        return versions

class DBLPSearchMethod(ArticleSearchMethod):
    name = SearchMethod.DBLP_SEARCH.value
    DBLP_URL = "https://dblp.org/search/publ/api?q={query}&format=json"
    DBLP_BIB_URL = "https://dblp.org/rec/{key}.bib"
    
    def _search_info(self, query: str):
        """Return the raw DBLP ``info`` dict for the first hit with a valid venue,
        or None. Kept separate from ``search`` so ``get_bibtex`` can read the DBLP
        key (which ArticleData does not store)."""
        headers = {"User-Agent": "Mozilla/5.0 (compatible; dblp_search/1.0)"}
        response = requests.get(self.DBLP_URL.format(query=query), headers=headers, timeout=60)
        response.raise_for_status()
        data = response.json()
        hits = data.get("result", {}).get("hits", {}).get("hit", [])
        for hit in hits:
            venue = hit.get("info", {}).get("venue", "")
            if venue and check_valid_venue(venue):
                return hit["info"]
        return None

    def search(self, query: str):
        info = self._search_info(query)
        if info is None:
            return None
        return ArticleData(
            id=hashlib.sha256(info["title"].encode()).hexdigest(),
            title=info.get("title", ""),
            venue=info.get("venue", ""),
            pub_year=str(info.get("year", "0")),
            doi=normalize_doi(info.get("doi", "")),
            search_method=self.name,
        )

    def get_bibtex(self, article: ArticleData):
        info = self._search_info(article.title)
        if info is None:
            return None
        dblp_key = info["key"]
        headers = {"User-Agent": "Mozilla/5.0 (compatible; dblp_search/1.0)"}
        response = requests.get(self.DBLP_BIB_URL.format(key=dblp_key), headers=headers, timeout=60)
        response.raise_for_status()
        if response.status_code != 200:
            print(f"{DBLP_SEARCH_TAG} No bibtex found for", article.title)
            return None
        bibtex = response.text
        return bibtex
    
    def get_snowballing_articles(self, citedby: str, **kwargs):
        
        iteration = kwargs.get("iteration", 0)
        backwards = kwargs.get("backwards", True)
        forwards = kwargs.get("forwards", False)
        if forwards:
            print(f"{DBLP_SEARCH_TAG} Forward search not supported")
        if backwards:
            print(f"{DBLP_SEARCH_TAG} Forward search not supported")
        return []

class SemanticScholarSearchMethod(ArticleSearchMethod):
    name = SearchMethod.SEMANTIC_SCHOLAR.value
    search_fields = "title,authors,paperId,citationStyles,venue,year,url,citationCount,externalIds,abstract"
    snowballing_fields = "title,authors,paperId,citations,references,citationCount"
    citation_fields = "title,authors,paperId,venue,year,openAccessPdf,citationCount,externalIds,abstract"
    reference_fields = "title,authors,paperId,venue,year,url,citationCount,externalIds,abstract"

    def get_article_data(self, pub, pub_id, iteration: int = 0, selected: SelectionStage = SelectionStage.NOT_SELECTED, new_pub: bool = False, search_method: str = ""):
        article_data = {
            "id": pub_id,
            "container_type": pub.get("container_type", ""),
            "eprint_url": pub.get("pub_url", "") if "eprint_url" not in pub else pub["eprint_url"],
            "source": pub.get("source", ""),
            "title": pub.get("title", ""),
            "authors": pub.get("authors", ""),
            "venue": pub.get("venue", ""),
            "pub_year": str(pub.get("pub_year", "0")),
            "pub_url": pub.get("eprint_url", pub.get("pub_url", "")),
            "num_citations": pub.get("num_citations", 0),
            "citedby_url": pub.get("citedby_url", ""),
            "url_related_articles": pub.get("url_related_articles", ""),
            "new_pub": new_pub,
            "selected": selected,
            "iteration": iteration,
            "search_method": search_method,
            "bibtex": pub.get("bibtex", ""),
            "duplicate": False,
            "keep_title": False,
            "keep_content": False,
            "search_method": search_method,
            "doi": pub.get("doi", ""),
            "abstract": pub.get("abstract", ""),
        }
        return ArticleData(**article_data)

    def search(self, query: str):
        url = _semantic_scholar_search_url(query, self.search_fields)
        response = _semantic_scholar_get(url)
        if response.status_code in (404, 429):
            print(f"{SS_SEARCH_TAG} Article not found for", query) if response.status_code == 404 else print(
                f"{SS_SEARCH_TAG} Rate limit exceeded for", query, "- skipping after retries"
            )
            return None
        response.raise_for_status()
        data = response.json()
        if not data.get("data"):
            print(f"{SS_SEARCH_TAG} No results for", query)
            return None
        pub = self.map_to_pub(data["data"][0])
        article_data = self.get_article_data(pub, pub["paperId"], new_pub=True, selected=SelectionStage.NOT_SELECTED, search_method=self.name)
        return article_data
    
    def map_to_pub(self, article: dict):
        """
        {"contexts": [], 
        "isInfluential": false, 
        "intents": [], 
        "citingPaper": 
        {"paperId": "8c19883bc33282985af977fd3f3cc4b6ac5b15d3", 
        "title": "Determining the Credibility of Science Communication", 
        "openAccessPdf": {"url": "https://aclanthology.org/2021.sdp-1.1.pdf", "status": "HYBRID", "license": "CCBY", "disclaimer": "Notice: Paper or abstract available at https://arxiv.org/abs/2105.14473, which is subject to the license by the author or copyright owner provided with this content. Please go to the source to verify the license and copyright information for your use."}, 
        "authors": [{"authorId": "1736067", "name": "Isabelle Augenstein"}], 
        "abstract": "Most work on scholarly document processing assumes that the information processed is trust-worthy and factually correct. However, this is not always the case. There are two core challenges, which should be addressed: 1) ensuring that scientific publications are credible \u2013 e.g. that claims are not made without supporting evidence, and that all relevant supporting evidence is provided; and 2) that scientific findings are not misrepresented, distorted or outright misreported when communicated by journalists or the general public. I will present some first steps towards addressing these problems and outline remaining challenges."}}
        """
        open_access_pdf = article.get("openAccessPdf") or {}
        external_ids = article.get("externalIds") or {}
        return {
            "title": article.get("title", ""),
            "venue": article.get("venue", "") or "",
            "pub_year": article.get("year", "0"),
            "paperId": article.get("paperId", ""),
            "authors": article.get("authors", ""),
            "eprint_url": open_access_pdf.get("url", "") or article.get("url", "") or "",
            "num_citations": article.get("citationCount", 0),
            "doi": normalize_doi(external_ids.get("DOI", "")),
            "abstract": article.get("abstract", "") or "",
        }
    
    def get_bibtex(self, article: ArticleData):
        if not article.id:
            return None

        article_id = article.id
        if article.search_method != SearchMethod.SEMANTIC_SCHOLAR.value:
            id_url = _semantic_scholar_search_url(article.title)
            id_response = _semantic_scholar_get(id_url)
            id_response.raise_for_status()
            id_data = id_response.json()
            if not id_data.get("data"):
                return None
            article_id = id_data["data"][0]["paperId"]

        bibtex_url = f"{SS_API_BASE}/paper/{article_id}?fields=citationStyles"
        try:
            response = _semantic_scholar_get(bibtex_url)
            response.raise_for_status()
            if response.status_code != 200:
                print(f"{SS_SEARCH_TAG} Bibtex not found for", article.title)
                return None
            data = response.json()
            return data["citationStyles"]["bibtex"]
        except Exception as e:
            print(f"{SS_SEARCH_TAG} Error getting bibtex for", article.title, ":", e)
            return None
    
    def _get_citedby(self, citedby: str):
        all_citations = []
        offset = 0
        limit = 100

        while True:
            query_url = (
                f"{SS_API_BASE}/paper/{citedby}/citations"
                f"?fields={self.citation_fields}&offset={offset}&limit={limit}"
            )
            response = _semantic_scholar_get(query_url)
            if response.status_code != 200:
                print(f"{SS_SEARCH_TAG} Citations not found for", citedby)
                return None
            data = response.json()

            citations = data.get("data", [])
            if not citations:
                break

            all_citations.extend(citations)
            offset += limit

        return all_citations

    def _get_referencedby(self, citedby: str):
        all_references = []
        offset = 0
        limit = 100

        while True:
            query_url = (
                f"{SS_API_BASE}/paper/{citedby}/references"
                f"?fields={self.reference_fields}&offset={offset}&limit={limit}"
            )
            response = _semantic_scholar_get(query_url)
            if response.status_code != 200:
                print(f"{SS_SEARCH_TAG} References not found for", citedby)
                return None
            data = response.json()

            references = data.get("data", [])
            if not references:
                break

            all_references.extend(references)
            offset += limit

        return all_references
 
    def get_snowballing_articles(self, citedby: str, **kwargs):
        """Return snowballing results as ``(ArticleData, direction)`` tuples.

        ``direction`` is ``'citation'`` (the paper cites the seed; forward
        snowballing, provider kwarg ``backwards``) or ``'reference'`` (the seed
        cites the paper; backward snowballing, provider kwarg ``forwards``).
        """
        if not citedby:
            return []
        backwards = kwargs.get("backwards", False)
        forwards = kwargs.get("forwards", False)
        iteration = kwargs.get("iteration", 0)
        citations = []
        references = []
        if backwards:
            citations = self._get_citedby(citedby) or []
        if forwards:
            references = self._get_referencedby(citedby) or []
        articles = []
        for citation in citations:
            pub = self.map_to_pub(citation.get("citingPaper") or {})
            if not pub.get("paperId"):
                continue
            article = self.get_article_data(pub, pub["paperId"], iteration, new_pub=True, search_method=self.name)
            articles.append((article, "citation"))
        for reference in references:
            pub = self.map_to_pub(reference.get("citedPaper") or {})
            if not pub.get("paperId"):
                continue
            article = self.get_article_data(pub, pub["paperId"], iteration, new_pub=True, search_method=self.name)
            articles.append((article, "reference"))

        return articles
    

OPENALEX_SEARCH_TAG = "[OpenAlex]"
OPENALEX_API_BASE = "https://api.openalex.org"
OPENALEX_MAX_RETRIES = 6
_openalex_last_request_time = 0.0


def _openalex_mailto() -> str:
    """Optional contact for OpenAlex's faster "polite pool". Only used if the user
    explicitly sets OPENALEX_MAILTO; never derived from their account."""
    return os.getenv("OPENALEX_MAILTO", "")


def _openalex_get(url: str, params: dict | None = None, timeout: int = 60) -> requests.Response:
    """GET from OpenAlex with light rate pacing and retry on HTTP 429."""
    global _openalex_last_request_time
    min_interval = 0.12  # ~8 req/s, comfortably under OpenAlex's 10 req/s limit
    request_params = dict(params or {})
    mailto = _openalex_mailto()
    if mailto:
        request_params["mailto"] = mailto
    backoff = 1
    response = None
    for attempt in range(OPENALEX_MAX_RETRIES + 1):
        elapsed = time.time() - _openalex_last_request_time
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)
        response = requests.get(url, params=request_params, timeout=timeout)
        _openalex_last_request_time = time.time()
        if response.status_code != 429:
            return response
        if attempt >= OPENALEX_MAX_RETRIES:
            print(f"{OPENALEX_SEARCH_TAG} Rate limit exceeded after {OPENALEX_MAX_RETRIES} retries")
            return response
        retry_after = response.headers.get("Retry-After")
        wait = int(retry_after) if retry_after and str(retry_after).isdigit() else backoff
        print(f"{OPENALEX_SEARCH_TAG} Rate limit (429), waiting {wait}s (retry {attempt + 1}/{OPENALEX_MAX_RETRIES})...")
        time.sleep(wait)
        backoff = min(backoff * 2, 60)
    return response


def fetch_bibtex_from_doi(doi: str, timeout: int = 60) -> str | None:
    """Fetch a BibTeX record for a DOI via DOI content negotiation
    (https://doi.org/<doi> with Accept: application/x-bibtex). Works for any
    DOI-bearing record regardless of which provider found it."""
    if not doi:
        return None
    url = doi if str(doi).startswith("http") else f"https://doi.org/{doi}"
    try:
        response = requests.get(url, headers={"Accept": "application/x-bibtex"}, timeout=timeout)
    except requests.exceptions.RequestException:
        return None
    if response.status_code == 200 and response.text.strip().startswith("@"):
        return response.text
    return None


class OpenAlexSearchMethod(ArticleSearchMethod):
    """Search via the OpenAlex API (https://openalex.org).

    Free and key-less, with broad coverage. Supports both forward snowballing
    (papers citing the seed, via ``filter=cites:<id>``) and backward snowballing
    (the seed's ``referenced_works``), matching Semantic Scholar's capability.
    BibTeX is obtained through DOI content negotiation.
    """
    name = SearchMethod.OPENALEX.value

    @staticmethod
    def _short_id(openalex_id: str) -> str:
        # "https://openalex.org/W2741809807" -> "W2741809807"
        if not openalex_id:
            return ""
        return str(openalex_id).rstrip("/").rsplit("/", 1)[-1]

    @staticmethod
    def _reconstruct_abstract(inverted_index: dict) -> str:
        """OpenAlex returns abstracts as an inverted index {word: [positions]};
        reconstruct the plain-text abstract from it."""
        if not inverted_index:
            return ""
        positions = {}
        for word, idxs in inverted_index.items():
            for i in idxs:
                positions[i] = word
        return " ".join(positions[i] for i in sorted(positions))

    def map_to_pub(self, work: dict) -> dict:
        """Map an OpenAlex ``work`` object to the internal pub dict."""
        if not work:
            return {"paperId": ""}
        source = (work.get("primary_location") or {}).get("source") or {}
        venue = source.get("display_name") or (work.get("host_venue") or {}).get("display_name", "") or ""
        authors = ", ".join(
            (a.get("author") or {}).get("display_name", "")
            for a in (work.get("authorships") or [])
            if (a.get("author") or {}).get("display_name")
        )
        open_access = work.get("open_access") or {}
        doi = work.get("doi") or ""
        return {
            "paperId": self._short_id(work.get("id", "")),
            "title": work.get("title") or work.get("display_name") or "",
            "venue": venue,
            "pub_year": work.get("publication_year") or "0",
            "authors": authors,
            "eprint_url": open_access.get("oa_url") or doi or "",
            "num_citations": work.get("cited_by_count", 0),
            "doi": normalize_doi(doi),
            "abstract": self._reconstruct_abstract(work.get("abstract_inverted_index")),
            "referenced_works": work.get("referenced_works") or [],
        }

    def get_article_data(self, pub, pub_id, iteration: int = 0, selected: SelectionStage = SelectionStage.NOT_SELECTED, new_pub: bool = False, search_method: str = ""):
        return ArticleData(
            id=pub_id,
            title=pub.get("title", ""),
            venue=pub.get("venue", "") or "",
            pub_year=str(pub.get("pub_year", "0")),
            authors=pub.get("authors", ""),
            eprint_url=pub.get("eprint_url", ""),
            pub_url=pub.get("eprint_url", ""),
            num_citations=pub.get("num_citations", 0),
            new_pub=new_pub,
            selected=selected,
            iteration=iteration,
            search_method=search_method,
            doi=pub.get("doi", ""),
            abstract=pub.get("abstract", ""),
        )

    def search(self, query: str):
        print(f"{OPENALEX_SEARCH_TAG} Searching for", query)
        response = _openalex_get(f"{OPENALEX_API_BASE}/works", params={"search": query, "per_page": 1})
        if response.status_code == 404:
            return None
        response.raise_for_status()
        results = response.json().get("results", [])
        if not results:
            print(f"{OPENALEX_SEARCH_TAG} No results for", query)
            return None
        pub = self.map_to_pub(results[0])
        if not pub.get("paperId"):
            return None
        return self.get_article_data(pub, pub["paperId"], new_pub=True, selected=SelectionStage.NOT_SELECTED, search_method=self.name)

    def get_bibtex(self, article: ArticleData):
        """Resolve the article on OpenAlex, then fetch BibTeX via its DOI."""
        if not getattr(article, "title", ""):
            return None
        response = _openalex_get(f"{OPENALEX_API_BASE}/works", params={"search": article.title, "per_page": 1})
        if response.status_code != 200:
            return None
        results = response.json().get("results", [])
        if not results:
            return None
        doi = results[0].get("doi") or ""
        if not doi:
            print(f"{OPENALEX_SEARCH_TAG} No DOI (and thus no BibTeX) for", article.title)
            return None
        return fetch_bibtex_from_doi(doi)

    def _get_citing_works(self, openalex_id: str) -> list:
        """Forward snowballing: all works that cite ``openalex_id`` (cursor-paged)."""
        works = []
        cursor = "*"
        while cursor:
            response = _openalex_get(
                f"{OPENALEX_API_BASE}/works",
                params={"filter": f"cites:{openalex_id}", "per_page": 200, "cursor": cursor},
            )
            if response.status_code != 200:
                print(f"{OPENALEX_SEARCH_TAG} Citations not found for", openalex_id)
                break
            data = response.json()
            works.extend(data.get("results", []))
            cursor = (data.get("meta") or {}).get("next_cursor")
        return works

    def _get_referenced_works(self, openalex_id: str) -> list:
        """Backward snowballing: metadata for the work's ``referenced_works``."""
        response = _openalex_get(f"{OPENALEX_API_BASE}/works/{openalex_id}")
        if response.status_code != 200:
            print(f"{OPENALEX_SEARCH_TAG} References not found for", openalex_id)
            return []
        ref_ids = [self._short_id(r) for r in (response.json().get("referenced_works") or [])]
        works = []
        # Batch-fetch referenced works' metadata (OpenAlex OR-filter, chunks of 50).
        for i in range(0, len(ref_ids), 50):
            chunk = [rid for rid in ref_ids[i:i + 50] if rid]
            if not chunk:
                continue
            batch = _openalex_get(
                f"{OPENALEX_API_BASE}/works",
                params={"filter": "openalex:" + "|".join(chunk), "per_page": 50},
            )
            if batch.status_code != 200:
                continue
            works.extend(batch.json().get("results", []))
        return works

    def get_snowballing_articles(self, citedby: str, **kwargs):
        """Return ``(ArticleData, direction)`` tuples. ``direction`` is 'citation'
        (forward; provider kwarg ``backwards``) or 'reference' (backward; provider
        kwarg ``forwards``), matching the Semantic Scholar method's convention."""
        if not citedby:
            return []
        backwards = kwargs.get("backwards", False)
        forwards = kwargs.get("forwards", False)
        iteration = kwargs.get("iteration", 0)
        articles = []
        if backwards:
            for work in self._get_citing_works(citedby):
                pub = self.map_to_pub(work)
                if not pub.get("paperId"):
                    continue
                articles.append((self.get_article_data(pub, pub["paperId"], iteration, new_pub=True, search_method=self.name), "citation"))
        if forwards:
            for work in self._get_referenced_works(citedby):
                pub = self.map_to_pub(work)
                if not pub.get("paperId"):
                    continue
                articles.append((self.get_article_data(pub, pub["paperId"], iteration, new_pub=True, search_method=self.name), "reference"))
        return articles


CROSSREF_SEARCH_TAG = "[Crossref]"
CROSSREF_API_BASE = "https://api.crossref.org"
CROSSREF_MAX_RETRIES = 6
_crossref_last_request_time = 0.0


def _crossref_mailto() -> str:
    """Optional contact for Crossref's "polite pool". Only used if the user sets
    CROSSREF_MAILTO; never derived from their account."""
    return os.getenv("CROSSREF_MAILTO", "")


def _crossref_get(url: str, params: dict | None = None, timeout: int = 60) -> requests.Response:
    """GET from Crossref with light rate pacing and retry on HTTP 429."""
    global _crossref_last_request_time
    min_interval = 0.12
    request_params = dict(params or {})
    mailto = _crossref_mailto()
    if mailto:
        request_params["mailto"] = mailto
    headers = {"User-Agent": f"ProfOlaf/1.0 (mailto:{mailto})" if mailto else "ProfOlaf/1.0"}
    backoff = 1
    response = None
    for attempt in range(CROSSREF_MAX_RETRIES + 1):
        elapsed = time.time() - _crossref_last_request_time
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)
        response = requests.get(url, params=request_params, headers=headers, timeout=timeout)
        _crossref_last_request_time = time.time()
        if response.status_code != 429:
            return response
        if attempt >= CROSSREF_MAX_RETRIES:
            print(f"{CROSSREF_SEARCH_TAG} Rate limit exceeded after {CROSSREF_MAX_RETRIES} retries")
            return response
        retry_after = response.headers.get("Retry-After")
        wait = int(retry_after) if retry_after and str(retry_after).isdigit() else backoff
        print(f"{CROSSREF_SEARCH_TAG} Rate limit (429), waiting {wait}s (retry {attempt + 1}/{CROSSREF_MAX_RETRIES})...")
        time.sleep(wait)
        backoff = min(backoff * 2, 60)
    return response


class CrossrefSearchMethod(ArticleSearchMethod):
    """Search via the Crossref REST API (https://api.crossref.org).

    Free and key-less, DOI-centric, with very broad coverage of published works.
    BibTeX is obtained through DOI content negotiation. Snowballing is limited to
    *backward* (the work's deposited ``reference`` list); Crossref does not expose a
    "who cites this" endpoint, so forward snowballing is unsupported (like Google
    Scholar / DBLP). References without a DOI are skipped (no stable id).
    """
    name = SearchMethod.CROSSREF.value

    @staticmethod
    def _clean_doi(doi: str) -> str:
        return normalize_doi(doi)

    @staticmethod
    def _first(values) -> str:
        if isinstance(values, list):
            return values[0] if values else ""
        return values or ""

    def _authors(self, work: dict) -> str:
        return ", ".join(
            " ".join(filter(None, [a.get("given"), a.get("family")])).strip()
            for a in (work.get("author") or [])
            if a.get("given") or a.get("family")
        )

    def _year(self, work: dict):
        date_parts = ((work.get("issued") or {}).get("date-parts") or [])
        if date_parts and date_parts[0]:
            return date_parts[0][0]
        return "0"

    def map_to_pub(self, work: dict) -> dict:
        if not work:
            return {"paperId": ""}
        doi = self._clean_doi(work.get("DOI", ""))
        return {
            "paperId": doi,  # Crossref is DOI-centric; DOI is the stable id
            "title": self._first(work.get("title")),
            "venue": self._first(work.get("container-title")),
            "pub_year": self._year(work),
            "authors": self._authors(work),
            "eprint_url": work.get("URL", "") or (f"https://doi.org/{doi}" if doi else ""),
            "num_citations": work.get("is-referenced-by-count", 0),
            "doi": doi,
            "abstract": work.get("abstract", "") or "",
        }

    def _map_reference(self, ref: dict) -> dict:
        """Map one Crossref reference entry to a pub dict (DOI required for an id)."""
        doi = self._clean_doi(ref.get("DOI", ""))
        if not doi:
            return {"paperId": ""}
        title = ref.get("article-title") or ref.get("volume-title") or ref.get("unstructured") or ""
        return {
            "paperId": doi,
            "title": title,
            "venue": ref.get("journal-title", "") or "",
            "pub_year": ref.get("year", "") or "0",
            "authors": ref.get("author", "") or "",
            "eprint_url": f"https://doi.org/{doi}",
            "num_citations": 0,
            "doi": doi,
        }

    def get_article_data(self, pub, pub_id, iteration: int = 0, selected: SelectionStage = SelectionStage.NOT_SELECTED, new_pub: bool = False, search_method: str = ""):
        return ArticleData(
            id=pub_id,
            title=pub.get("title", ""),
            venue=pub.get("venue", "") or "",
            pub_year=str(pub.get("pub_year", "0")),
            authors=pub.get("authors", ""),
            eprint_url=pub.get("eprint_url", ""),
            pub_url=pub.get("eprint_url", ""),
            num_citations=pub.get("num_citations", 0),
            new_pub=new_pub,
            selected=selected,
            iteration=iteration,
            search_method=search_method,
            doi=pub.get("doi", ""),
            abstract=pub.get("abstract", ""),
        )

    def search(self, query: str):
        print(f"{CROSSREF_SEARCH_TAG} Searching for", query)
        response = _crossref_get(f"{CROSSREF_API_BASE}/works", params={"query.bibliographic": query, "rows": 1})
        if response.status_code == 404:
            return None
        response.raise_for_status()
        items = (response.json().get("message") or {}).get("items", [])
        if not items:
            print(f"{CROSSREF_SEARCH_TAG} No results for", query)
            return None
        pub = self.map_to_pub(items[0])
        if not pub.get("paperId"):
            return None
        return self.get_article_data(pub, pub["paperId"], new_pub=True, selected=SelectionStage.NOT_SELECTED, search_method=self.name)

    def get_bibtex(self, article: ArticleData):
        """BibTeX via DOI content negotiation. Uses the article's own DOI id when it
        was found through Crossref, otherwise re-resolves it by title."""
        doi = ""
        if getattr(article, "search_method", "") == self.name:
            doi = self._clean_doi(getattr(article, "id", ""))
        if not doi:
            if not getattr(article, "title", ""):
                return None
            response = _crossref_get(f"{CROSSREF_API_BASE}/works", params={"query.bibliographic": article.title, "rows": 1})
            if response.status_code != 200:
                return None
            items = (response.json().get("message") or {}).get("items", [])
            if not items:
                return None
            doi = self._clean_doi(items[0].get("DOI", ""))
        if not doi:
            return None
        return fetch_bibtex_from_doi(doi)

    def _get_references(self, doi: str) -> list:
        response = _crossref_get(f"{CROSSREF_API_BASE}/works/{doi}")
        if response.status_code != 200:
            print(f"{CROSSREF_SEARCH_TAG} References not found for", doi)
            return []
        return ((response.json().get("message") or {}).get("reference") or [])

    def get_snowballing_articles(self, citedby: str, **kwargs):
        """Backward snowballing only (the work's references). ``direction`` is
        'reference' (provider kwarg ``forwards``). Forward snowballing (citations,
        provider kwarg ``backwards``) is not available from Crossref."""
        if not citedby:
            return []
        backwards = kwargs.get("backwards", False)
        forwards = kwargs.get("forwards", False)
        iteration = kwargs.get("iteration", 0)
        if backwards:
            print(f"{CROSSREF_SEARCH_TAG} Forward snowballing (citations) not supported")
        if not forwards:
            return []
        articles = []
        for ref in self._get_references(self._clean_doi(citedby)):
            pub = self._map_reference(ref)
            if not pub.get("paperId"):
                continue
            articles.append((self.get_article_data(pub, pub["paperId"], iteration, new_pub=True, search_method=self.name), "reference"))
        return articles


class ArticleSearch:
    def __init__(self, method: ArticleSearchMethod):
        self.method = method
    
    def set_method(self, method: ArticleSearchMethod):
        self.method = method

    def search(self, query: str):
        return self.method.search(query)

    def get_all_versions_bibtexes(self, pub):
        """Safely call get_all_versions if the strategy supports it"""
        try:
            return self.method.get_all_versions_bibtexes(pub)
        except NotImplementedError as e:
            print(f"Warning: {e}")
            return None

    def get_bibtex(self, pub):
        return self.method.get_bibtex(pub)

    def get_snowballing_articles(self, citedby: str, **kwargs):
        return self.method.get_snowballing_articles(citedby, **kwargs)
    