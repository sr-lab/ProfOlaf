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
    def get_all_versions(self, pub):
        """Optional method - raises NotImplementedError by default"""
        raise NotImplementedError(f"{self.__class__.__name__} does not support get_all_versions")
    
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

    def get_bibtex(self, query: scholarly.Publication):
        bibtex = scholarly.bibtex(query) 
        if bibtex is None:
            print("No bibtex found for", query.get("title", ""))
            return None
        return bibtex
    
    def get_all_versions(self, pub: scholarly.Publication):
        return scholarly.get_all_versions(pub)

class DBLPSearchMethod(ArticleSearchMethod):
    DBLP_URL = "https://dblp.org/search/publ/api?q={query}&format=json"
    def search(self, query: str):
        response = requests.get(self.DBLP_URL.format(query=query))
        data = response.json()
        info = None
        if data.get("result", {}).get("hits", {}).get("hit", []) != []:
            for hit in data["result"]["hits"]["hit"]:
                if hit["info"]["venue"] and check_valid_venue(hit["info"]["venue"]):
                    info = hit["info"]
                    break
        if info is None:
            print("No valid venue found for", query)
            return None
        pub = {}
        pub["authors"] = [author["text"] for author in info["authors"]["author"]]
        pub["venue"] = info["venue"]
        pub["title"] = info["title"]
        pub["pub_year"] = info["year"]
        return get_article_data(
            info, 
            hashlib.md5(query.encode('utf-8')).hexdigest(), 
            new_pub=True, 
            selected=SelectionStage.NOT_SELECTED
        )
    
    def get_bibtex(self, query):
        article_data = self.search(query)
        if article_data is None:
            return None
        db = BibDatabase()
        db.entries = [article_data.dict]
        writer = BibTexWriter()
        writer.indent = '    '
        writer.order_entries_by = None
        bibtex = writer.write(db)
        return bibtex
    

class ArticleSearch:
    def __init__(self, method: ArticleSearchMethod):
        self.method = method
    
    def set_method(self, method: ArticleSearchMethod):
        self.method = method

    def search(self, query: str):
        return self.method.search(query)

    def get_all_versions(self, pub):
        """Safely call get_all_versions if the strategy supports it"""
        try:
            return self.method.get_all_versions(pub)
        except NotImplementedError as e:
            print(f"Warning: {e}")
            return None

    def get_bibtex(self, pub: scholarly.Publication):
        return self.method.get_bibtex(pub)

    def get_citedby(self, citedby: str):
        return self.method.get_citedby(citedby)