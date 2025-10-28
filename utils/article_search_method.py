from abc import ABC, abstractmethod
import sys
import time
from scholarly import scholarly
import requests
from utils.db_management import ArticleData, SelectionStage
import hashlib
import re
import bibtexparser
from bibtexparser.bwriter import BibTexWriter
from bibtexparser.bibdatabase import BibDatabase
from enum import Enum
from crossref import CrossRefAPIClient


DBLP_SEARCH_TAG = "[DBLP]"
SS_SEARCH_TAG = "[Semantic Scholar]"
GS_SEARCH_TAG = "[Google Scholar]"

class SearchMethod(Enum):
    GOOGLE_SCHOLAR = "google_scholar"
    SEMANTIC_SCHOLAR = "semantic_scholar"
    DBLP_SEARCH = "dblp"
    
    def get_search_class(self):
        """Get the corresponding search method class for this enum value."""
        if self == SearchMethod.GOOGLE_SCHOLAR:
            return GoogleScholarSearchMethod
        elif self == SearchMethod.SEMANTIC_SCHOLAR:
            return SemanticScholarSearchMethod
        elif self == SearchMethod.DBLP_SEARCH:
            return DBLPSearchMethod
        else:
            raise ValueError(f"Unknown search method: {self}")
    
    def create_instance(self):
        """Create an instance of the corresponding search method class."""
        return self.get_search_class()()



def check_valid_venue(venue: str):
    return venue != "" and "arxiv" not in venue.lower() \
        and "corr" not in venue.lower() \
        and "no title" not in venue.lower()

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
        pub = scholarly.search_single_pub(query)
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
            
            articles.append(self.get_article_data(pub, pub_id, iteration, new_pub=True, search_method=self.name))
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
    
    def search(self, query: str):
        headers = {"User-Agent": "Mozilla/5.0 (compatible; dblp_search/1.0)"}
        response = requests.get(self.DBLP_URL.format(query=query), headers=headers, timeout=60)
        response.raise_for_status()
        data = response.json()
        info = None
        if data.get("result", {}).get("hits", {}).get("hit", []) != []:
            for hit in data["result"]["hits"]["hit"]:
                venue = hit.get("info", {}).get("venue", "")
                if venue and check_valid_venue(venue):
                    info = hit["info"]
                    break
        if info is None:
            #print(f"{DBLP_SEARCH_TAG} No valid venue found for", query)
            return None
        return {
            "title": info["title"],
            "venue": info["venue"],
            "pub_year": info["year"],
            "key": info["key"],
        }
    
    def get_bibtex(self, article: ArticleData):
        dblp_data = self.search(article.title)
        if dblp_data is None:
            return None
        dblp_key = dblp_data["key"]
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
    search_query = "https://api.semanticscholar.org/graph/v1/paper/search/match?query={query}&fields=title,authors,paperId,citationStyles,venue,year,url"
    bibtex_query = "https://api.semanticscholar.org/graph/v1/paper/{paper_id}?fields=citationStyles"
    snowballing_query = "https://api.semanticscholar.org/graph/v1/paper/search/match?query={query}&fields=title,authors,paperId,citations,references"
    citations_query = "https://api.semanticscholar.org/graph/v1/paper/{paper_id}/citations?fields=title,authors,paperId,venue,year,openAccessPdf"
    references_query = "https://api.semanticscholar.org/graph/v1/paper/{paper_id}/references?fields=title,authors,paperId,venue,year,url"

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
            "num_citations": pub.get("citations", 0),
            "citedby_url": pub.get("citedby_url", ""),
            "url_related_articles": pub.get("url_related_articles", ""),
            "new_pub": new_pub,
            "selected": selected,
            "iteration": iteration,
            "search_method": search_method,
            "bibtex": pub.get("bibtex", ""),
            "duplicate": False,
            "title_reason": "",
            "content_reason": "",
            "duplicate": False,
            "search_method": search_method,
        }
        return ArticleData(**article_data)

    def search(self, query: str):
        response = requests.get(self.search_query.format(query=query), timeout=60)
        response.raise_for_status()
        if response.status_code != 200:
            print(f"{SS_SEARCH_TAG} Article not found for", query)
            return None
        data = response.json()
        pub = self.map_to_pub(data["data"][0])
        # Return articleData
        return self.get_article_data(pub, pub["paperId"], new_pub=True, selected=SelectionStage.NOT_SELECTED, search_method=self.name)
    
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
        return {
            "title": article["title"],
            "venue": article["venue"],
            "pub_year": article["year"],
            "paperId": article["paperId"],
            "authors": article["authors"],
            "eprint_url": article.get("openAcessPdf", {}).get("url", "") or article.get("url", ""),
        }
    
    def get_bibtex(self, article: ArticleData):
        initial_delay = 1
        response = requests.get(self.bibtex_query.format(paper_id=article.id), timeout=60)
        
        while response.status_code == 429:
            print(f"{SS_SEARCH_TAG} Rate limit exceeded for", article.title)
            retry_after = response.headers.get("Retry-After")
            retry_after = int(response.headers.get("Retry-After", initial_delay))
            print(f"Rate limited. Waiting {retry_after} seconds...")
            time.sleep(retry_after)
            response = requests.get(self.bibtex_query.format(paper_id=article.id), timeout=60)
            initial_delay *= 2 
        try:
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
        limit = 100  # Maximum allowed by the API
        
        while True:
            query_url = f"{self.citations_query.format(paper_id=citedby)}&offset={offset}&limit={limit}"
            response = requests.get(query_url, timeout=60)
            response.raise_for_status()
            if response.status_code != 200:
                print(f"{SS_SEARCH_TAG} Citations not found for", citedby)
                return None
            data = response.json()
            
            citations = data.get("data", [])
            
            # If no more citations, break
            if not citations:
                print("No more citations")
                break
            
            all_citations.extend(citations)
            offset += limit
            
        return all_citations

        
    def _get_referencedby(self, citedby: str):
        all_references = []
        offset = 0
        limit = 100  # Maximum allowed by the API
        
        while True:
            query_url = f"{self.references_query.format(paper_id=citedby)}&offset={offset}&limit={limit}"
            response = requests.get(query_url, timeout=60)
            response.raise_for_status()
            if response.status_code != 200:
                print(f"{SS_SEARCH_TAG} References not found for", citedby)
                return None
            data = response.json()
            
            references = data.get("data", [])
            
            # If no more references, break
            if not references:
                print("No more references")
                break
            
            all_references.extend(references)
            offset += limit

        return all_references
 
    def get_snowballing_articles(self, citedby: str, **kwargs):
        backwards = kwargs.get("backwards", False)
        forwards = kwargs.get("forwards", False)
        iteration = kwargs.get("iteration", 0)
        citations = []
        references = []
        if backwards:
            citations = self._get_citedby(citedby)
        if forwards:
            references = self._get_referencedby(citedby)
        articles = []
        for citation in citations:
            pub = self.map_to_pub(citation["citingPaper"])
            articles.append(self.get_article_data(pub, pub["paperId"], iteration, new_pub=True, search_method=self.name))
        for reference in references:
            pub = self.map_to_pub(reference["citedPaper"])
            articles.append(self.get_article_data(pub, pub["paperId"], iteration, new_pub=True, search_method=self.name))

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
    