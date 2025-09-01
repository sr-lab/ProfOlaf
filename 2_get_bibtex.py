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

load_dotenv()

pg = get_proxy()

def get_bibtex(iteration: int, article: ArticleData):
    current_wait_time = 30
    while True:
        try:
            query = scholarly.search_pubs(article.title)
            pub = next(query)
            bibtex = pub.bibtex
        except:
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
    parser.add_argument('--db_path', help='db path', type=str)
    args = parser.parse_args()

    db_manager = initialize_db(args.db_path, args.iteration)
    articles = db_manager.get_iteration_data(args.iteration)
    
    for article in articles:
        get_bibtex(args.iteration, article)
        
