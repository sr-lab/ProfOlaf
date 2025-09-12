import sys, re, os, json, csv, html, difflib

import bibtexparser
import argparse
from typing import List, Set, Dict, Tuple
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from utils.db_management import (
    ArticleData,
    DBManager
)

with open("search_conf.json", "r") as f:
    search_conf = json.load(f)

def input_venue_rank(venue: str):
    # FIXME: Add Scimago and Core Table search
    """
    Input the rank of the venue.
    """
    while True:
        rank = input(f"What is the rank of this venue? ")
        if rank not in ["A*", "A", "B", "C", "D", "Q1", "Q2", "Q3", "Q4", "NA"]:
            print("Invalid rank.")
        else:
            break
    return rank

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
    print(similar_venues)
    for i, (similar_venue, sim) in enumerate(similar_venues, 1):
        print(f"{i}. {similar_venue} (Similarity: {sim:.3f})")
    print("\nOptions:")
    print("0. None of these - enter new rank")
    for i in range(len(similar_venues)):
        print(f"{i + 1}. {similar_venues[i][0]}")
    while True:
        try:
            choice = int(input("Choose an option: "))
            if 0 <= choice <= len(similar_venues):
                break
            else:
                print("Invalid choice. Please try again.")
        except ValueError:
            print("Invalid input. Please enter a number.")
    
    if choice == 0:
        return None
    else:
        return conf_rank[similar_venues[choice-1][0]]
    
def get_unindexed_venues(venues: Set[str], conf_rank: Dict[str, str]):
    """
    Get the unindexed venues.
    """
    unindexed_venues = []
    for i, venue in enumerate(venues):
        if venue.lower() not in [k.lower() for k in conf_rank.keys()]:
            if "arXiv" in venue or "arxiv" in venue or "SSRN" in venue:
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
            if rank is None:
                rank = input_venue_rank(venue)
        else:
            rank = input_venue_rank(venue)
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
