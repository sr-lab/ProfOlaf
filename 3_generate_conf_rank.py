import re
import os
import json
import sys
import bibtexparser
import argparse
from typing import List, Set, Dict
from utils.db_management import (
    ArticleData,
    initialize_db
)

def input_venue_rank():
    while True:
        rank = input(f"What is the rank of this venue? ")
        if rank not in ["A*", "A", "B", "C", "D", "Q1", "Q2", "Q3", "Q4", "NA"]:
            print("Invalid rank.")
        else:
            break
    return rank

def get_venues(articles: List[ArticleData]):
    venues = set()
    for article in articles:
        if article.bibtex != "":
            print(article.bibtex)
            library = bibtexparser.loads(article.bibtex)
            if library.entries[0]["ENTRYTYPE"] in ["book", "phdthesis", "mastersthesis"]:
                continue
            if "booktitle" in library.entries[0]:
                venues.add(library.entries[0]["booktitle"])
            elif "journal" in library.entries[0]:
                venues.add(library.entries[0]["journal"])
    return venues

def get_unindexed_venues(venues: Set[str], conf_rank: Dict[str, str]):
    unindexed_venues = []
    for i, venue in enumerate(venues):
        if venue not in conf_rank.keys():
            if "arXiv" in venue or "arxiv" in venue or "SSRN" in venue:
                rank = "NA"
                conf_rank[venue] = rank
                db_manager.insert_conf_rank_data([(venue, rank)])
                continue
            unindexed_venues.append(venue)

    for i, venue in enumerate(unindexed_venues):
        print(f"({i + 1}/{len(unindexed_venues)})", venue)
        rank = input_venue_rank()

        conf_rank[venue] = rank
        db_manager.insert_conf_rank_data([(venue, rank)])

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Generate conf rank')
    parser.add_argument('--iteration', help='iteration number', type=int)
    args = parser.parse_args()

    db_manager = initialize_db(args.iteration)
    articles = db_manager.get_iteration_data(args.iteration)

    venues = get_venues(articles)

    conf_rank = db_manager.get_conf_rank_data()
    conf_rank = {venue: rank for venue, rank in conf_rank}
    get_unindexed_venues(venues, conf_rank)
