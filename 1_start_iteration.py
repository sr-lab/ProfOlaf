import argparse
import json
import re
import time
import sys
from scholarly import scholarly
from dotenv import load_dotenv
import hashlib
from utils.proxy_generator import get_proxy
from utils.db_management import (
    DBManager, 
    get_article_data,
    SelectionStage
)
from utils.article_search_method import (
    ArticleSearch, 
    GoogleScholarSearchMethod, 
)

load_dotenv()
with open("search_conf.json", "r") as f:
    search_conf = json.load(f)

pg = get_proxy(search_conf["proxy_key"])

def get_articles(iteration: int, initial_pubs, db_manager: DBManager, article_search: ArticleSearch):
    """
    Get articles that cite the pubs for the given iteration.
    """
    while len(initial_pubs) > 0:
        citedby = initial_pubs.pop().id
        if not str(citedby).isdigit():
            continue
        pubs = article_search.get_citedby(citedby)
        print("Citedby: ", citedby, "Total Results: ", pubs.total_results)
        sys.stdout.flush()

        articles = []
        for pub in pubs:
            print("Pub: ", pub["bib"]["title"])
            if db_manager.get_seen_title(pub["bib"]["title"]) is not None:
                print("Seen title found for", pub["bib"]["title"])
                continue
            if "citedby_url" not in pub:
                print("No citedby_url found for", pub["bib"]["title"], "replacing it with hashed title")
                pub_id = hashlib.sha256(pub["bib"]["title"].encode()).hexdigest()
            else:
                m = re.search("cites=[\d+,]*", pub["citedby_url"])
                pub_id = m.group()[6:]
            
            articles.append(get_article_data(pub, pub_id, iteration, new_pub=True))
            sys.stdout.flush()

        print("Articles: ", len(articles))

        db_manager.insert_iteration_data(articles)
        db_manager.insert_seen_titles_data([(article.title, article.id) for article in articles])
    sys.stdout.flush()
    db_manager.cursor.close()
    db_manager.conn.close()
    
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Generate snowball sampling starting points from file')
    parser.add_argument('--iteration', help='iteration number', type=int, required=True)
    parser.add_argument('--db_path', help='db path', type=str, default=search_conf["db_path"])  
    args = parser.parse_args()
    db_manager = DBManager(args.db_path)
    
    initial_pubs = db_manager.get_iteration_data(
        iteration=args.iteration - 1, 
        selected=SelectionStage.ABSTRACT_INTRO_APPROVED
    )
    
    print("Initial Pubs: ", len(initial_pubs))
    sys.stdout.flush()
    article_search = ArticleSearch(GoogleScholarSearchMethod())
    get_articles(args.iteration, initial_pubs, db_manager, article_search)
