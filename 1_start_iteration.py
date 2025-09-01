import argparse
import re
import time
import traceback
import os
import sys
from scholarly import scholarly
from dotenv import load_dotenv
import hashlib
from utils.proxy_generator import get_proxy
from utils.db_management import (
    DBManager, 
    get_article_data,
    initialize_db
)

load_dotenv()
#pg = get_proxy()

def get_articles(iteration: int, initial_pubs, db_manager: DBManager):
    while len(initial_pubs) > 0:
        current_wait_time = 30
        citedby = list(initial_pubs.pop())[0]
        if not str(citedby).isdigit():
            continue
        while True:
            try:
                pubs = scholarly.search_citedby(int(citedby))
                print("pubs", pubs)
            except:
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

        article_data = []

        for pub in pubs:
            if "citedby_url" not in pub:
                print("No citedby_url found for", pub["bib"]["title"], "replacing it with hashed title")
                pub_id = hashlib.sha256(pub["bib"]["title"].encode()).hexdigest()
            else:
                m = re.search("cites=[\d+,]*", pub["citedby_url"])
                pub_id = m.group()[6:]

            article_data.append(get_article_data(pub, pub_id, new_pub=True))

        db_manager.insert_iteration_data(iteration, article_data)
        db_manager.insert_seen_titles_data([(article_data.title, article_data.id) for article_data in article_data])

    db_manager.cursor.close()
    db_manager.conn.close()
    
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Generate snowball sampling starting points from file')
    parser.add_argument('--iteration', help='iteration number', type=int, default=0)
    parser.add_argument('--db_path', help='db path', type=str)  
    args = parser.parse_args()
    db_manager = initialize_db(args.db_path, args.iteration)
    
    initial_pubs = db_manager.get_iteration_data(args.iteration - 1)
    
    print("Initial Pubs: ", len(initial_pubs))
    sys.stdout.flush()


    get_articles(args.iteration, initial_pubs, db_manager)
