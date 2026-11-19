import bibtexparser
import json
import os
import re
import sys

from langdetect import detect
from rich import print

from ..db_management import DBManager, SelectionStage
from ..cli.pretty_print_utils import format_color_string

with open("confs/search_conf.json", "r") as f:
    search_conf = json.load(f)

PREPRINT_VENUE_PATTERNS = (
    r"\barxiv\b",
    r"\bcorr\b",
    r"\bcoRR\b",
    r"\bssrn\b",
    r"\bopenreview\b",
    r"\bbiorxiv\b",
    r"\bmedrxiv\b",
    r"\bpreprint\b",
)

PREPRINT_URL_PATTERNS = (
    r"arxiv\.org",
    r"openreview\.net",
    r"ssrn\.com",
    r"biorxiv\.org",
    r"medrxiv\.org",
)


def _text_indicates_preprint(text: str) -> bool:
    if not text:
        return False
    text_lower = text.lower()
    return any(re.search(pattern, text_lower) for pattern in PREPRINT_VENUE_PATTERNS)


def _url_indicates_preprint(url: str) -> bool:
    if not url:
        return False
    url_lower = url.lower()
    return any(re.search(pattern, url_lower) for pattern in PREPRINT_URL_PATTERNS)


def _bibtex_indicates_preprint(bibtex: str) -> bool:
    if not bibtex:
        return False
    try:
        library = bibtexparser.loads(bibtex)
        if not library.entries:
            return False
        entry = library.entries[0]
        for field in ("journal", "booktitle", "howpublished", "publisher", "venue"):
            value = entry.get(field, "")
            if value and _text_indicates_preprint(str(value)):
                return True
        archive_prefix = entry.get("archiveprefix", entry.get("archivePrefix", ""))
        if str(archive_prefix).lower() == "arxiv":
            return True
        eprint = entry.get("eprint", "")
        if str(eprint).lower().startswith("arxiv"):
            return True
    except Exception:
        return False
    return False


def is_preprint_publication(
    venue: str = "",
    bibtex: str = "",
    eprint_url: str = "",
    pub_url: str = "",
) -> tuple[bool, str | None]:
    """
    Detect non-peer-reviewed preprints (arXiv, CORR, SSRN, OpenReview, etc.).

    Checks venue metadata, download URLs, and BibTeX fields (not article titles).
    """
    for url in (eprint_url, pub_url):
        if _url_indicates_preprint(url):
            return True, f"preprint URL: {url}"
    if _text_indicates_preprint(venue):
        return True, f"preprint venue: {venue}"
    if _bibtex_indicates_preprint(bibtex):
        return True, "preprint indicators in BibTeX"
    return False, None

def is_year_valid(pub_year):
    """
    Check if the publication is between the start and end year.
    """
    pub_year = int(pub_year) if pub_year.isdigit() else 0   
    if pub_year != 0 and (pub_year < int(search_conf["start_year"]) or pub_year > int(search_conf["end_year"])):
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

def filter_elements(
    db_manager: DBManager,
    iteration: int,
    disable_venue_check,
    disable_year_check,
    disable_english_check,
    disable_download_check,
    reject_preprints: bool = True,
):
    """
    Filter the elements by metadata.
    """
    # If we are filtering by venue, year or download, we need to get all articles that have a bibtex, otherwise we get all articles that are not selected.
    bibtex_not_empty = True if not (disable_venue_check and disable_year_check and disable_download_check) else False
    if bibtex_not_empty:
        print("Getting all articles that have a bibtex")
        article_data = db_manager.get_iteration_data(
            iteration=iteration, 
            bibtex__not_empty=True, 
            bibtex__ne="NO_BIBTEX", 
            selected=SelectionStage.NOT_SELECTED
        )
    else:
        print("Getting all articles that are not selected")
        article_data = db_manager.get_iteration_data(
            iteration=iteration, 
            selected=SelectionStage.NOT_SELECTED
        )
    print("article_data", len(article_data))
    updated_data = []
    articles_kept_counter = 0
    for i, article in enumerate(article_data):
        print("\n-----------------------------------------------------")
        print(f"Element {i+1} out of {len(article_data)}: {format_color_string(article.title, 'blue', 'bold')}")
        print(f"Venue: {format_color_string(article.venue, 'magenta', 'bold')}")
        print(f"Url: {format_color_string(article.eprint_url, 'green', 'bold')}")

        if reject_preprints:
            is_preprint, reason = is_preprint_publication(
                venue=article.venue or "",
                bibtex=article.bibtex or "",
                eprint_url=article.eprint_url or "",
                pub_url=article.pub_url or "",
            )
            if is_preprint:
                print(f"This paper is a non-peer-reviewed preprint ({reason})")
                article.venue_filtered_out = True
                updated_data.append((article.id, article.venue_filtered_out, "venue_filtered_out"))
                continue

        if not disable_venue_check and not is_venue_and_peer_reviewed(article.bibtex, db_manager):
            print(f"This paper was not peer reviewed or it is not in one of the following ranks: {format_color_string(search_conf['venue_rank_list'], 'red', 'bold')}")
            article.venue_filtered_out = True
            updated_data.append((article.id, article.venue_filtered_out, "venue_filtered_out"))
            continue
        if not disable_year_check and not is_year_valid(article.pub_year):
            print(f"This paper was not published between {format_color_string(search_conf['start_year'], 'red', 'bold')} and {format_color_string(search_conf['end_year'], 'red', 'bold')}")
            article.year_filtered_out = True
            updated_data.append((article.id, article.year_filtered_out, "year_filtered_out"))
            continue
        if not disable_english_check and not is_in_english(article.title, db_manager):
            print(f"This paper is not in English")
            article.language_filtered_out = True
            updated_data.append((article.id, article.language_filtered_out, "language_filtered_out"))
            continue      
        if not disable_download_check and not is_downloadable(article.eprint_url):
            print(f"This paper is not available for download")
            article.download_filtered_out = True
            updated_data.append((article.id, article.download_filtered_out, "download_filtered_out"))
            continue

        articles_kept_counter += 1
        print(f"This paper was selected")
        article.selected = SelectionStage.METADATA_APPROVED
        updated_data.append((article.id, article.selected, "selected"))
        print("-----------------------------------------------------")

    
    db_manager.update_batch_iteration_data(iteration, updated_data)
    print(f"Kept {format_color_string(articles_kept_counter, 'green', 'bold')} out of {format_color_string(len(article_data), 'green', 'bold')} articles")
