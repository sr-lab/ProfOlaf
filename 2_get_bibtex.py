import os
import time
import sys
import json
from scholarly import scholarly
from scholarly import ProxyGenerator
from dotenv import load_dotenv
import argparse
from utils.db_management import (
    DBManager, 
    ArticleData,
    get_article_data,
    initialize_db
)
from utils.proxy_generator import get_proxy
import bibtexparser

load_dotenv()
with open("search_conf.json", "r") as f:
    search_conf = json.load(f)
pg = get_proxy(search_conf["proxy_key"])


def get_alternative_bibtexes(pub):
    versions = scholarly.get_all_versions(pub.title)
    for version in versions:
        booktitle = version.get("booktitle", "")
        journal = version.get("journal", "")
        venue = version.get("venue", "")
        final_venue = booktitle or journal or venue
        if final_venue and "arxiv" not in final_venue.lower() \
            and "corr" not in final_venue.lower():
            return version.bibtex()
    return ""

def get_bibtex_venue(bibtex: str):
    if bibtex != "":
        library = bibtexparser.loads(article.bibtex)
        if library.entries[0]["ENTRYTYPE"] in ["book", "phdthesis", "mastersthesis"]:
            return ""
        if "booktitle" in library.entries[0]:
            return library.entries[0]["booktitle"]
        elif "journal" in library.entries[0]:
            return library.entries[0]["journal"]
    
    return ""

def get_bibtex(iteration: int, article: ArticleData):
    """
    Get the bibtex string for the given article.
    """
    current_wait_time = 30
    while True:
        try:
            query = scholarly.search_single_pub(article.title)
            pub = next(query)
            bibtex = scholarly.bibtex(pub)
            venue = get_bibtex_venue(bibtex)
            if "arxiv" not in venue.lower():
                continue
            alternative_bibtex = get_alternative_bibtexes(pub)
            if alternative_bibtex == "":
                continue
            bibtex = alternative_bibtex
        except Exception as e:
            print(e)
            print(f"Retrying {article}, waiting {current_wait_time}...", file=sys.stderr)
            sys.stdout.flush()
            time.sleep(current_wait_time)
            current_wait_time *= 2
            continue
        break
    db_manager.update_iteration_data(iteration, article.id, bibtex=bibtex)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Get bibtex for articles')
    parser.add_argument('--iteration', help='iteration number', type=int)
    parser.add_argument('--db_path', help='db path', type=str, default=search_conf["db_path"])
    args = parser.parse_args()

    db_manager = DBManager(args.db_path)
    articles = db_manager.get_iteration_data(iteration=args.iteration)
    
    for article in articles:
        get_bibtex(args.iteration, article)
        
