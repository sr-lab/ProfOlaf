from abc import ABC, abstractmethod
import sys
import time
from scholarly import scholarly
import requests
from utils.db_management import ArticleData, get_article_data, SelectionStage
import hashlib
import re
import bibtexparser
from bibtexparser.bwriter import BibTexWriter
from bibtexparser.bibdatabase import BibDatabase

from crossref import CrossRefAPIClient



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
    # Optional methods that some strategies may implement
    def get_all_versions_bibtexes(self, pub):
        """Optional method - raises NotImplementedError by default"""
        raise NotImplementedError(f"{self.__class__.__name__} does not support get_all_versions_bibtexes")
    
    def get_citedby(self, citedby: str):
        """Optional method - raises NotImplementedError by default"""
        raise NotImplementedError(f"{self.__class__.__name__} does not support search_citedby")

class GoogleScholarSearchMethod(ArticleSearchMethod):

    def search(self, query: str):
        pub = scholarly.search_single_pub(query)
        if pub is None:
            print("No results found for", query)
            return None
        scholar_id = pub.get('citedby_url')
        id = hashlib.md5(query.encode('utf-8')).hexdigest() if scholar_id is None else 0
        if id == 0:
            match = re.search(r"cites=(\d+)", scholar_id)
            if match is None:
                print("No match found for", query)
                return None
            id = int(match.group(1))
        return get_article_data(pub, id, new_pub=True, selected=SelectionStage.NOT_SELECTED)

    def get_citedby(self, citedby: str):
        current_wait_time = 30
        while True:
            try:
                pubs = scholarly.search_citedby(int(citedby))
                break
            except Exception as e:
                print(e)
                print(f"Retrying {citedby}, waiting {current_wait_time}...", file=sys.stderr)
                sys.stdout.flush()
                time.sleep(current_wait_time)
                current_wait_time *= 2
                continue
        if pubs.total_results == 0:
            print("No results found for", citedby)
            return []
        return pubs

    def get_bibtex(self, pub):
        bibtex = scholarly.bibtex(pub) 
        if bibtex is None:
            print("No bibtex found for", pub["title"])
            return None
        return bibtex
    
    def get_all_versions_bibtexes(self, pub):
        versions = scholarly.get_all_versions_bibtexes(pub)
        print("Versions:", versions)
        if versions is None or versions == []:
            print("No versions found")
            return []
        for version in versions:
            version = bibtexparser.loads(version)
        return versions

class DBLPSearchMethod(ArticleSearchMethod):
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
            print("No valid venue found for", query, "in dblp")
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
        bibtex = response.text
        return bibtex
    

class SemanticScholarSearchMethod(ArticleSearchMethod):
    search_query = "https://api.semanticscholar.org/graph/v1/paper/search/match?query={query}&fields=title,authors,paperId,citationStyles,venue"
    snowballing_query = "https://api.semanticscholar.org/graph/v1/paper/search/match?query={query}&fields=title,authors,paperId,citations,references"
    def search(self, query: str):
        response = requests.get(self.search_query.format(query=query), timeout=60)
        response.raise_for_status()
        data = response.json()
        return data
    
    def get_bibtex(self, article: ArticleData):
        response = requests.get(self.search_query.format(query=article.title), timeout=60)
        response.raise_for_status()
        data = response.json()
        return data
    
    def get_citedby(self, citedby: str):
        pass
    

method = SemanticScholarSearchMethod()
data = method.search("Proof Flow: Preliminary Study on Generative Flow Network Language Model Tuning for Formal Reasoning")
print(data)

#for item in data["message"]["items"]:
#    print(item["title"])


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

    def get_citedby(self, citedby: str):
        return self.method.get_citedby(citedby)