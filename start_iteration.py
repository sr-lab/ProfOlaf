import re
import time
import traceback
import os
import sys
import json
from scholarly import scholarly
from dotenv import load_dotenv

from utils.db_management import DBManager
from utils.proxy_generator import get_proxy

load_dotenv()
#pg = get_proxy()

db_manager = DBManager("prof_olaf.db")


def get_pub_info(pub, pub_id):
    pub_info = {}
    pub_info["id"] = pub_id
    pub_info["container_type"] = pub["container_type"]
    pub_info["source"] = pub["source"]
    pub_info["title"] = pub["bib"]["title"]
    pub_info["authors"] = pub["bib"]["author"]
    pub_info["venue"] = pub["bib"]["venue"]
    pub_info["pub_year"] = pub["bib"]["pub_year"]
    pub_info["pub_url"] = pub["pub_url"]
    pub_info["num_citations"] = pub["num_citations"]
    pub_info["citedby_url"] = pub["citedby_url"]
    pub_info["url_related_articles"] = pub["url_related_articles"]
    return pub_info

def get_articles(initial_pubs, db_manager: DBManager):
    new_pubs = []
    #print("initial_pubs", initial_pubs)
    while len(initial_pubs) > 0:
        current_wait_time = 30
        citedby = list(initial_pubs.pop().keys())[0]    
        #print("citedby", citedby)
        if not citedby.isdigit():
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

        cite_ids = []

        for pub in pubs:
            if pub['bib']['title'] in seen_titles:
                continue
            seen_titles.append(pub['bib']['title'])
            if "citedby_url" not in pub:
                continue
            m = re.search("cites=[\d+,]*", pub["citedby_url"])
            pub_id = m.group()[6:]
            cite_ids.append(pub_id)
            new_pubs.append(pub_id)

            pub_info = get_pub_info(pub, pub_id)
            db_manager.insert_data(iteration, pub_info)

    db_manager.cursor.close()
    db_manager.conn.close()
    
if __name__ == "__main__":
    iteration = int(sys.argv[1])
    db_manager.create_table(iteration)

    with open("test_files/iteration_0.json", "r") as f:
        data = json.load(f)
        initial_pubs = data["initial_pubs"]
        seen_titles = data["seen_titles"]
        new_pubs = data["new_pubs"]

    print("Initial Pubs: ", len(initial_pubs))
    sys.stdout.flush()

    get_articles(initial_pubs, db_manager)
