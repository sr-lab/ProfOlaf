import sys, re, os, json, csv, html, difflib
import bibtexparser
import argparse
from typing import List, Set, Dict, Tuple
from itertools import zip_longest

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity


from tabulate import tabulate

from utils.db_management import (
    ArticleData,
    DBManager
)
from utils.scimago_search import find_scimago_rank
from utils.core_table_search import search_core_table, load_core_table


with open("search_conf.json", "r") as f:
    search_conf = json.load(f)


def _get_scimago_rank(venue: str, as_string: bool = False):
    while True:
        venue_name = input(f"Enter the name of the venue (leave it blank to use {venue} or 'q' to quit): ")
        if venue_name == "q":
            return None, None, None
        if venue_name == "":
            venue_name = venue
        venue_name = venue_name.lower()
        try:
            best, rank, categories = find_scimago_rank(venue_name)
            if as_string:
                string = f"'{best.title}' ({best.similarity_score})\n\nCategories:\n"
                for category, bucket in categories.items():
                    string += f"  {category}: {bucket['current']['quartile']}\n"
                return string
            else:
                return best, rank, categories
        except Exception as e:
            print(f"Error searching scimago for {venue_name}: {e}")
            continue


def _get_core_rank(venue: str, as_string: bool = False):
    core_rank = search_core_table(venue, load_core_table("ranking_tables/core_table2.csv"), top_k=1)
    best = core_rank[0]
    if as_string:
        best.similarity_score = round(best.similarity_score, 2)
        string = f"'{best.title}' ({best.similarity_score})\n\n"
        string += f"Core Rank: {best.rank}\n"
        return string
    else:
        return best, best.rank

def search_rank_databases(venue: str):
    while True:
        scimago_rank = _get_scimago_rank(venue, as_string=True)
        core_rank = _get_core_rank(venue, as_string=True)
        table = tabulate(
            [(scimago_rank, core_rank)], 
            headers=["Scimago", "Core Table"], 
            tablefmt="grid",
            stralign="left",
            colalign=("left", "left"),
            disable_numparse=True,
            maxcolwidths=[None, None]
        )
        print(table)

        while True:
            final_rank = input("Enter the rank for the venue (leave it blank to go back to manual input): ")
            if final_rank and final_rank in search_conf["venue_rank_list"]:
                break
            else:
                print("Invalid rank.")
                continue
        if final_rank:
            return final_rank
        else:
            return None
        

def get_venues(articles: List[ArticleData]):
    """
    Get the venues from the articles.
    """
    venues = set()
    for article in articles:
        if article.bibtex != "":
            library = bibtexparser.loads(article.bibtex)
            if library.entries[0]["ENTRYTYPE"] in ["book", "phdthesis", "mastersthesis"]:
                continue
            if "booktitle" in library.entries[0]:
                venues.add(library.entries[0]["booktitle"])
            elif "journal" in library.entries[0]:
                venues.add(library.entries[0]["journal"])
    return venues

def find_similar_venues(venue: str, existing_venues: Set[str], threshold: float = 0.5, top_k: int = 5) -> List[str]:
    """
    Find the similar venues.
    """
    
    similar_venues = []
    if not existing_venues:
        return []
    vectorizer = TfidfVectorizer()
    all_venues = [venue] + list(existing_venues)
    tfidf_matrix = vectorizer.fit_transform(all_venues)
    cosine_similarities = cosine_similarity(tfidf_matrix[0:1], tfidf_matrix[1:]).flatten()

    similar_venues = []
    for i, sim in enumerate(cosine_similarities):
        if sim > threshold:
            similar_venues.append((list(existing_venues)[i], sim))
    similar_venues.sort(key=lambda x: x[1], reverse=True)
  
    return similar_venues[:top_k]

def prompt_similar_venues(venue: str, similar_venues: List[Tuple[str, float]], conf_rank: Dict[str, str]) -> str:
    print(f"\nFound {len(similar_venues)} similar venues to '{venue}':")
    table = tabulate(similar_venues, headers=["Venue", "Similarity"], tablefmt="grid", stralign="left", colalign=("left", "left"), disable_numparse=True, maxcolwidths=[None, None])
    print(table)
    rank = None
    while rank is None:
        try:
            rank = int(input("Enter the rank for the venue: "))
            if rank == "":
                rank = None
        except ValueError:
            print("Invalid input. Please enter a number.")
    
    if rank == "":
        return None
    else:
        return rank
    
def get_unindexed_venues(venues: Set[str], conf_rank: Dict[str, str]):
    """
    Get the unindexed venues.
    """
    # Check if venue is arxiv, ssrn or corr is the venue
    unindexed_venues = []
    for i, venue in enumerate(venues):
        if venue.lower() not in [k.lower() for k in conf_rank.keys()]:
            if "arxiv" in venue.lower() or "ssrn" in venue.lower() or 'corr' in venue.lower():
                rank = "NA"
                conf_rank[venue] = rank
                db_manager.insert_conf_rank_data([(venue, rank)])
                continue
            unindexed_venues.append(venue)

    for i, venue in enumerate(unindexed_venues):
        print(f"({i + 1}/{len(unindexed_venues)})", venue)
        similar_venues = find_similar_venues(venue, conf_rank.keys())
        if similar_venues:
            rank = prompt_similar_venues(venue, similar_venues, conf_rank)
            while rank is None:
                rank = search_rank_databases(venue)
        else:
            rank = search_rank_databases(venue)
        conf_rank[venue] = rank
        db_manager.insert_conf_rank_data([(venue, rank)])

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Generate conf rank')
    parser.add_argument('--iteration', help='iteration number', type=int)
    parser.add_argument('--db_path', help='db path', type=str, default=search_conf["db_path"])
    args = parser.parse_args()

    db_manager = DBManager(args.db_path)
    articles = db_manager.get_iteration_data(iteration=args.iteration)

    venues = get_venues(articles)

    conf_rank = db_manager.get_conf_rank_data()
    conf_rank = {venue: rank for venue, rank in conf_rank}
    get_unindexed_venues(venues, conf_rank)
