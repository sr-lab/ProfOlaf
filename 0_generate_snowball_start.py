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
from enum import Enum
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
    SearchMethod,
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
    
def generate_snowball_start(input_file: str, iteration: int, delay: float = 2.0, db_manager: DBManager = None, search_method: SearchMethod = SearchMethod.GOOGLE_SCHOLAR):
    """
    Generate snowball sampling starting points from accepted papers.
    
    Args:
        input_file: Path to the input JSON file (e.g., accepted_papers.json)
        iteration: Iteration number for the search
        delay: Delay between requests to avoid rate limiting
        db_manager: Database manager instance
        search_method: Search method to use (SearchMethod enum)
    """
    print(f"Reading titles from {input_file}...")
    titles = extract_titles_from_file(input_file)
    if not titles:
        print("No titles found in the input file.")
        return
    print(f"Found {len(titles)} titles. Starting searches with {search_method.value}...")
    
    search_method_instance = search_method.create_instance()
    article_search = ArticleSearch(search_method_instance)
    
    initial_pubs = []
    seen_titles = []
    for i, title in tqdm(enumerate(titles, 1), total=len(titles), desc=f"Searching with {search_method.value}"):      
        article_data = article_search.search(title)
        print("Article data: ", article_data)
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
                       help='Delay between API requests in seconds (default: 1.0)')
    parser.add_argument('--db_path', help='db path', type=str, default=search_conf["db_path"])
    parser.add_argument(
        '--search_method', 
        help='Search method to use', 
        type=str, 
        default=search_conf["search_method"],
        choices=[method.value for method in SearchMethod]
    )
    args = parser.parse_args()

    # Convert string to enum
    try:
        search_method = SearchMethod(args.search_method)
    except ValueError:
        print(f"Error: Invalid search method '{args.search_method}'. Available options: {[method.value for method in SearchMethod]}")
        return

    db_manager = initialize_db(args.db_path)
    generate_snowball_start(args.input_file, ITERATION_0, args.delay, db_manager, search_method)


if __name__ == "__main__":
    main()
