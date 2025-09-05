import sys
import os
import bibtexparser
import json
import argparse

from utils.db_management import (
    DBManager, 
    initialize_db,
    SelectionStage
)

with open("search_conf.json", "r") as f:
    search_conf = json.load(f)


def check_year(pub_year):
    """
    Check if the publication is between the start and end year.
    """
    pub_year = int(pub_year) if pub_year.isdigit() else 0   
    if pub_year != 0 and (pub_year < search_conf["start_year"] or pub_year > search_conf["end_year"]):
        return False
    if pub_year == 0:
        while True:
            user_input = input(f"Is the publication year between {search_conf['start_year']} and {search_conf['end_year']} (y/n): ").strip().lower()
            if user_input == 'y':
                return True
            if user_input == 'n':
                return False
            else:
                print("Please enter 'y' for yes or 'n' for no.")
    return True

def check_english(bibtex, db_manager):
    """
    Check if the publication is in English.
    """
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
    """
    Check if the publication is available for download.
    """
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
    """
    Check the bibtex string to see if the publication is peer-reviewed and has a venue.
    """
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
            if venue_rank[0] in search_conf["venue_rank_list"]:
                return True
            else:
                return False
    
    return None
        
def check_venue_and_peer_reviewed(bibtex_path, db_manager):
    """
    Check if the publication is peer-reviewed and has a venue. If it is not present in the bibtex string, ask the user.
    """
    result = automated_check_venue_and_peer_reviewed(bibtex_path, db_manager)
    if result is not None:
        return result
    ranks = "or ".join(search_conf["venue_rank_list"])
    while True:
        user_input = input(f"Is the publication peer-reviewed and {ranks} (y/n): ").strip().lower()
        if user_input == 'y':
            return True
        if user_input == 'n':
            return False
        else:
            print("Please enter 'y' for yes or 'n' for no.")

def filter_elements(db_manager: DBManager, iteration: int):
    """
    Filter the elements by metadata.
    """
    article_data = db_manager.get_iteration_data(iteration=iteration)
    
    updated_data = []
    for i, article in enumerate(article_data):
        print()
        print(f"Element {i+1} out of {len(article_data)}")
        print(f"ID: {article.id}")
        print(f"Title: {article.title}")
        print(f"Venue: {article.venue}")
        print(f"Url: {article.eprint_url}")
    
        if not check_venue_and_peer_reviewed(article.bibtex, db_manager):
            print("Venue and peer-reviewed filtered out")
            article.venue_filtered_out = True
            updated_data.append((article.id, article.venue_filtered_out, "venue_filtered_out"))

        elif not check_year(article.pub_year):
            print("Year filtered out")
            article.year_filtered_out = True
            updated_data.append((article.id, article.year_filtered_out, "year_filtered_out"))
        elif not check_english(article.bibtex, db_manager):
            print("Language filtered out")
            article.language_filtered_out = True
            updated_data.append((article.id, article.language_filtered_out, "language_filtered_out"))
        elif not check_download(article.eprint_url):
            print("Download filtered out")
            article.download_filtered_out = True
            updated_data.append((article.id, article.download_filtered_out, "download_filtered_out"))
        else:
            print("Selected")
            article.selected = SelectionStage.SELECTED
            updated_data.append((article.id, article.selected, "selected"))

    db_manager.update_batch_iteration_data(iteration, updated_data)

def main(iteration, db_path):
    db_manager = initialize_db(db_path, iteration)
    filter_elements(db_manager, iteration)
        
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Filter by metadata')
    parser.add_argument('--iteration', help='iteration number', type=int)
    parser.add_argument('--db_path', help='db path', type=str, default=search_conf["db_path"])
    args = parser.parse_args()
    main(args.iteration, args.db_path)