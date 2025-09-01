import sys
import os
import bibtexparser
import json
import argparse

from utils.db_management import (
    DBManager, 
    ArticleData,
    get_article_data,
    initialize_db
)

def check_year(pub_year):
    # FIXME: Change hardcoded values
    if pub_year != 0 and pub_year < 2018:
        return False
    elif pub_year == 0:
        while True:
            user_input = input(f"Is the publication after 2018 (y/n): ").strip().lower()
            if user_input == 'y':
                return True
            if user_input == 'n':
                return False
            else:
                print("Please enter 'y' for yes or 'n' for no.")
    return True

def check_english(bibtex, db_manager):
    if automated_check_venue_and_peer_reviewed(bibtex, db_manager):
        return True

    while True:
        user_input = input(f"Is the publication in English (y/n): ").strip().lower()
        if user_input == 'y':
            return True
        if user_input == 'n':
            return False
        else:
            print("Please enter 'y' for yes or 'n' for no.")

def check_download(eprint_url):
    if eprint_url is not None and eprint_url != "":
        return True

    while True:
        user_input = input(f"Is the publication available for download (y/n): ").strip().lower()
        if user_input == 'y':
            return True
        if user_input == 'n':
            return False
        else:
            print("Please enter 'y' for yes or 'n' for no.")

def automated_check_venue_and_peer_reviewed(bibtex, db_manager):
    #FIXME: bibtex is stored as a string, not a path
    library = bibtexparser.loads(bibtex)
    venue = None
    if "booktitle" in library.entries[0]:
        venue = library.entries[0]["booktitle"]
    elif "journal" in library.entries[0]:
        venue = library.entries[0]["journal"]
    
    if (
        venue is None or 
        venue == "NA" or 
        library.entries[0]["ENTRYTYPE"] in ["book", "phdthesis", "mastersthesis"]
    ):
        return False
    
    if venue is not None:
        venue_rank = db_manager.get_venue_rank_data(venue)
        if venue_rank is not None:
            if venue_rank[0] in ["A", "A*", "Q1"]:
                return True
            else:
                return False
    
    return None
        
def check_venue_and_peer_reviewed(bibtex_path, db_manager):
    result = automated_check_venue_and_peer_reviewed(bibtex_path, db_manager)
    if result is not None:
        return result
        
    while True:
        user_input = input(f"Is the publication peer-reviewed and rank A/A* or Q1 (y/n): ").strip().lower()
        if user_input == 'y':
            return True
        if user_input == 'n':
            return False
        else:
            print("Please enter 'y' for yes or 'n' for no.")

def filter_elements(db_manager: DBManager, iteration: int):
    article_data = db_manager.get_iteration_data(iteration)
    
    updated_data = []
    for article in article_data:
        if not check_venue_and_peer_reviewed(article.bibtex, db_manager):
            article.venue_filtered_out = True
            updated_data.append((article.id, article.venue_filtered_out, "venue_filtered_out"))
        
        elif not check_year(article.pub_year):
            article.year_filtered_out = True
            updated_data.append((article.id, article.year_filtered_out, "year_filtered_out"))
        elif not check_english(article.bibtex, db_manager):
            article.language_filtered_out = True
            updated_data.append((article.id, article.language_filtered_out, "language_filtered_out"))
        elif not check_download(article.eprint_url):
            article.download_filtered_out = True
            updated_data.append((article.id, article.download_filtered_out, "download_filtered_out"))
        else:
            article.selected = True
            updated_data.append((article.id, article.selected, "selected"))

    db_manager.update_batch_iteration_data(iteration, updated_data)

def main(iteration, db_path):
    db_manager = initialize_db(db_path, iteration)
    filter_elements(db_manager, iteration)
        
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Filter by metadata')
    parser.add_argument('--iteration', help='iteration number', type=int)
    parser.add_argument('--db_path', help='db path', type=str)
    args = parser.parse_args()
    main(args.iteration, args.db_path)