#!/usr/bin/env python3
"""
Script to generate snowball sampling starting points from accepted papers.
Extracts titles from accepted_papers.json, searches Google Scholar for citation numbers,
and outputs in the format of initial.json.
"""

import json
import re
import time
import requests
from urllib.parse import quote_plus
import argparse
from typing import List, Dict, Optional
from scholarly import scholarly
from utils.proxy_generator import get_proxy
from tqdm import tqdm
import hashlib
from dotenv import load_dotenv
from utils.db_management import (
    DBManager, 
    initialize_db, 
    SelectionStage
)

from utils.article_search_method import (
    ArticleSearch, 
    GoogleScholarSearchMethod, 
)

ITERATION_0 = 0 

load_dotenv()
with open("search_conf.json", "r") as f:
    search_conf = json.load(f)

pg = get_proxy(search_conf["proxy_key"])

def extract_titles_from_file(file_path: str) -> List[str]:
    """
    Extract titles from a file. The file should be a text file with one title per line.
    """
    with open(file_path, 'r', encoding='utf-8') as f:
        return [line.strip() for line in f.readlines() if line.strip()]
    
def generate_snowball_start(input_file: str, iteration: int, delay: float = 2.0, db_manager: DBManager = None):
    """
    Generate snowball sampling starting points from accepted papers.
    
    Args:
        input_file: Path to the input JSON file (e.g., accepted_papers.json)
        output_file: Path to the output JSON file
        delay: Delay between Google Scholar requests to avoid rate limiting
    """
    print(f"Reading titles from {input_file}...")
    titles = extract_titles_from_file(input_file)
    if not titles:
        print("No titles found in the input file.")
        return
    print(f"Found {len(titles)} titles. Starting Google Scholar searches...")
    
    article_search = ArticleSearch(GoogleScholarSearchMethod())
    
    initial_pubs = []
    seen_titles = []
    for i, title in tqdm(enumerate(titles, 1), total=len(titles), desc="Searching for Google Scholar IDs"):      
        article_data = article_search.search(title)
        if article_data:
            article_data.set_iteration(iteration)
            article_data.set_selected(SelectionStage.CONTENT_APPROVED)
            initial_pubs.append(article_data)
            seen_titles.append((title, article_data.id))

        if i < len(titles):
            time.sleep(delay)

    db_manager.insert_iteration_data(initial_pubs)
    db_manager.insert_seen_titles_data(seen_titles)


def main():
    parser = argparse.ArgumentParser(description='Generate snowball sampling starting points from file')
    parser.add_argument('--input_file', help='Path to the input file (json or text)', default=search_conf["initial_file"])
    parser.add_argument('--delay', type=float, default=1.0, 
                       help='Delay between Google Scholar requests in seconds (default: 2.0)')
    parser.add_argument('--db_path', help='db path', type=str, default=search_conf["db_path"])
    args = parser.parse_args()

    db_manager = initialize_db(args.db_path)
    generate_snowball_start(args.input_file, ITERATION_0, args.delay, db_manager)


if __name__ == "__main__":
    main()
