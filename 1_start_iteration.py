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
    initialize_db,
    SelectionStage
)

load_dotenv()
with open("search_conf.json", "r") as f:
    search_conf = json.load(f)

pg = get_proxy(search_conf["proxy_key"])

def get_articles(iteration: int, initial_pubs, db_manager: DBManager):
    """
    Get articles that cite the pubs for the given iteration.
    """
    while len(initial_pubs) > 0:
        current_wait_time = 30
        citedby = initial_pubs.pop().id
        if not str(citedby).isdigit():
            continue
        while True:
            try:
                pubs = scholarly.search_citedby(int(citedby))
                print("pubs", pubs)
            except Exception as e:
                print(e)
                print(f"Retrying {citedby}, waiting {current_wait_time}...", file=sys.stderr)
                sys.stdout.flush()
                time.sleep(current_wait_time)
                current_wait_time *= 2
                continue
            break
        if pubs.total_results == 0:
            print("No citations found for", citedby)
        print("Cited by:", citedby, "Total Results:", pubs.total_results)
        sys.stdout.flush()

        articles = []

        for pub in pubs:
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

        print("Articles: ", len(articles))

        db_manager.insert_iteration_data(articles)
        db_manager.insert_seen_titles_data([(article.title, article.id) for article in articles])

    db_manager.cursor.close()
    db_manager.conn.close()
    
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Generate snowball sampling starting points from file')
    parser.add_argument('--iteration', help='iteration number', type=int, required=True)
    parser.add_argument('--db_path', help='db path', type=str, default=search_conf["db_path"])  
    args = parser.parse_args()
    db_manager = DBManager(args.db_path)
    
    initial_pubs = db_manager.get_iteration_data(iteration=args.iteration - 1, selected=SelectionStage.NOT_SELECTED)
    
    print("Initial Pubs: ", len(initial_pubs))
    sys.stdout.flush()
    
    get_articles(args.iteration, initial_pubs, db_manager)
