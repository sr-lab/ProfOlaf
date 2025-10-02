import sys
import os
import bibtexparser
import json
import argparse

from utils.db_management import (
    DBManager, 
    SelectionStage
)

from langdetect import detect

from rich import print

COLOR_START = "[bold magenta]"
COLOR_END = "[/bold magenta]"

def format_color_string(string: str):
    return f"{COLOR_START}{string}{COLOR_END}"

with open("search_conf.json", "r") as f:
    search_conf = json.load(f)


def is_year_valid(pub_year):
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

def is_in_english(title, db_manager):
    """
    Check if the publication is in English.
    """
    if detect(title) == "en":
        return True
    else:
        while True:
            user_input = input(f"Is the publication in English (y/n): ").strip().lower()
            if user_input == 'y':
                return True
            if user_input == 'n':
                return False
            else:
                print("Please enter 'y' for yes or 'n' for no.")

def is_downloadable(eprint_url):
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
    # Check if entries list is not empty before accessing first element
    if not library.entries:
        return False
    
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
        venue = venue.strip().replace("\n", " ")
        venue_rank = db_manager.get_venue_rank_data(venue)
        if venue_rank is not None:
            if venue_rank[0] in search_conf["venue_rank_list"]:
                return True
            else:
                return False
    
    return None
        
def is_venue_and_peer_reviewed(bibtex_path, db_manager):
    """
    Check if the publication is peer-reviewed and has a venue. If it is not present in the bibtex string, ask the user.
    """
    result = automated_check_venue_and_peer_reviewed(bibtex_path, db_manager)
    if result is not None:
        return result
    
    while True:
        user_input = input(f"Is the publication peer-reviewed and in one of the following ranks: {search_conf['venue_rank_list']} (y/n): ").strip().lower()
        if user_input == 'y':
            return True
        if user_input == 'n':
            return False
        else:
            print("Please enter 'y' for yes or 'n' for no.")

def filter_elements(db_manager: DBManager, iteration: int, disable_venue_check, disable_year_check, disable_english_check, disable_download_check):
    """
    Filter the elements by metadata.
    """
    article_data = db_manager.get_iteration_data(
        iteration=iteration, 
        bibtex__not_empty=True, 
        bibtex__ne="NO_BIBTEX", 
        selected=SelectionStage.NOT_SELECTED
    )
    
    updated_data = []
    articles_kept_counter = 0
    for i, article in enumerate(article_data):
        print("\n-----------------------------------------------------")
        print(f"Element {i+1} out of {len(article_data)}: {format_color_string(article.title)}")
        print(f"Venue: {format_color_string(article.venue)}")
        print(f"Url: {format_color_string(article.eprint_url)}")
    
        if not disable_venue_check and not is_venue_and_peer_reviewed(article.bibtex, db_manager):
            print("This paper was not peer reviewed or it is not in one of the following ranks: {search_conf['venue_rank_list']}")
            article.venue_filtered_out = True
            updated_data.append((article.id, article.venue_filtered_out, "venue_filtered_out"))
            continue
        if not disable_year_check and not is_year_valid(article.pub_year):
            print("This paper was not published between {search_conf['start_year']} and {search_conf['end_year']}")
            article.year_filtered_out = True
            updated_data.append((article.id, article.year_filtered_out, "year_filtered_out"))
            continue
        if not disable_english_check and not is_in_english(article.title, db_manager):
            print("This paper is not in English")
            article.language_filtered_out = True
            updated_data.append((article.id, article.language_filtered_out, "language_filtered_out"))
            continue      
        if not disable_download_check and not is_downloadable(article.eprint_url):
            print("This paper is not available for download")
            article.download_filtered_out = True
            updated_data.append((article.id, article.download_filtered_out, "download_filtered_out"))
            continue

        articles_kept_counter += 1
        print("This paper was selected")
        article.selected = SelectionStage.METADATA_APPROVED
        updated_data.append((article.id, article.selected, "selected"))
        print("-----------------------------------------------------")


    db_manager.update_batch_iteration_data(iteration, updated_data)
    print(f"Kept {articles_kept_counter} out of {len(article_data)} articles")

def main(iteration, db_path, disable_venue_check, disable_year_check, disable_english_check, disable_download_check):
    db_manager = DBManager(db_path)
    filter_elements(db_manager, iteration, disable_venue_check, disable_year_check, disable_english_check, disable_download_check)
        
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Filter by metadata')
    parser.add_argument('--iteration', help='iteration number', type=int, required=True)
    parser.add_argument('--db_path', help='db path', type=str, default=search_conf["db_path"])
    # Flags to disable different checks
    parser.add_argument('--disable_venue_check', help='disable venue check', action='store_true')
    parser.add_argument('--disable_year_check', help='disable year check', action='store_true')
    parser.add_argument('--disable_english_check', help='disable english check', action='store_true')
    parser.add_argument('--disable_download_check', help='disable download check', action='store_true')
    args = parser.parse_args()
    main(args.iteration, args.db_path, args.disable_venue_check, args.disable_year_check, args.disable_english_check, args.disable_download_check)