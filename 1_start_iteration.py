import argparse
import json
import re
import time
import sys
from scholarly import scholarly
from dotenv import load_dotenv
import hashlib

from tqdm import tqdm

from utils.proxy_generator import get_proxy
from utils.db_management import (
    DBManager, 
    SelectionStage
)
from utils.article_search_method import (
    ArticleSearch, 
    SearchMethod,
)

load_dotenv()
with open("search_conf.json", "r") as f:
    search_conf = json.load(f)

pg = get_proxy(search_conf["proxy_key"])

def get_articles(iteration: int, initial_pubs, db_manager: DBManager, article_search: ArticleSearch, verbose: bool = False):
    """
    Get articles that cite the pubs for the given iteration.
    """
    for initial_pub in tqdm(initial_pubs, desc="Getting articles from snowballing..."):
        citedby = initial_pub.id
        print("Citedby: ", citedby)
        
        articles = article_search.get_snowballing_articles(citedby, iteration=iteration, backwards=True, forwards=True)
        if len(articles) == 0:
            continue
        
        if verbose:
            print("Citedby: ", citedby, "Total Results: ", len(articles))

        sys.stdout.flush()

        filtered_articles = [article for article in articles if db_manager.get_seen_title(article.title) is None]
        if verbose:
            print(f"Found {len(filtered_articles)} new articles from {initial_pub.title}")

        db_manager.insert_iteration_data(filtered_articles)
        db_manager.insert_seen_titles_data([(article.title, article.id) for article in filtered_articles])
    
    sys.stdout.flush()
    db_manager.cursor.close()
    db_manager.conn.close()
    
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Generate snowball sampling starting points from file')
    parser.add_argument('--iteration', help='iteration number', type=int, required=True)
    parser.add_argument('--db_path', help='db path', type=str, default=search_conf["db_path"])
    parser.add_argument(
        '--search_method', 
        help='Search method to use', 
        type=str, 
        default=search_conf["search_method"],
        choices=[method.value for method in SearchMethod]
    )
    parser.add_argument('--verbose', action='store_true')  
    args = parser.parse_args()
    db_manager = DBManager(args.db_path)
    

    initial_pubs = db_manager.get_iteration_data(
        iteration=args.iteration - 1, 
        selected=SelectionStage.CONTENT_APPROVED,
        search_method=args.search_method
    )
    if len(initial_pubs) == 0:
        print("No initial pubs found")
        print("Possible reasons:")
        print("1. No initial pubs found for the given search method:", args.search_method)
        print("2. No initial pubs found for the given iteration:", args.iteration)
        print("Please run check_search_stage.py to better understand the current state of your search database")
        sys.exit(1)
    
    print("Initial Pubs: ", len(initial_pubs))
    sys.stdout.flush()
    search_method_instance = SearchMethod(args.search_method).create_instance()
    article_search = ArticleSearch(search_method_instance)
    get_articles(args.iteration, initial_pubs, db_manager, article_search, args.verbose)
