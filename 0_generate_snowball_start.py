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
    get_article_data,
    initialize_db, 
    SelectionStage
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
    
def extract_titles_from_json(json_file_path: str) -> List[str]:
    """
    Extract titles from a json file. The json file should have one exact key 'papers' and the value should be a list of dictionaries.
    Each dictionary should have one exact key 'title'.
    
    Args:
        json_file_path: Path to the JSON file
        
    Returns:
        List of paper titles
    """
    try:
        with open(json_file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        titles = []
        if 'papers' not in data:
            print(f"No papers found in {json_file_path}")
            return []
        for paper in data['papers']:
            if 'title' not in paper:
                print(f"No title found for paper: {paper}")
                continue
            titles.append(paper['title'])
        return titles
    except Exception as e:
        print(f"Error reading JSON file: {e}")
        return []
    

def search_google_scholar(title: str, iteration: int) -> Optional[int]:
    """
    Search Google Scholar for a paper title and extract the Google Scholar ID.
    Args:
        title: The paper title to search for
        
    Returns:
        The Google Scholar ID if found, None otherwise
    """
    try:
        result = scholarly.search_single_pub(title)

        if result is None:
            return None

        scholar_id = result.get('citedby_url')
        if scholar_id is None:
            print("No scholar_id found for", title)
            id = hashlib.md5(title.encode('utf-8')).hexdigest()
            article_data = get_article_data(result, id, iteration, new_pub=True, selected=SelectionStage.NOT_SELECTED)
            return article_data
        
        match = re.search(r"cites=(\d+)", scholar_id)
        if match is None:
            print("No match found for", title)
            return None
        id = int(match.group(1))
        article_data = get_article_data(result, id, iteration, new_pub=True, selected=SelectionStage.NOT_SELECTED)
        return article_data
    
    except Exception as e:
        print(f"Error searching for '{title}': {e}")
        return None

def generate_snowball_start(input_file: str, iteration: str, delay: float = 2.0, db_manager: DBManager = None):
    """
    Generate snowball sampling starting points from accepted papers.
    
    Args:
        input_file: Path to the input JSON file (e.g., accepted_papers.json)
        output_file: Path to the output JSON file
        delay: Delay between Google Scholar requests to avoid rate limiting
    """
    print(f"Reading titles from {input_file}...")

    if input_file.endswith('.json'):
        titles = extract_titles_from_json(input_file)
    elif input_file.endswith('.txt'):
        titles = extract_titles_from_file(input_file)
    else:
        print(f"Unsupported file type: {input_file}")
        return
    
    if not titles:
        print("No titles found in the input file.")
        return
    
    print(f"Found {len(titles)} titles. Starting Google Scholar searches...")
    
    initial_pubs = []
    seen_titles = []
    
    for i, title in tqdm(enumerate(titles, 1), total=len(titles), desc="Searching for Google Scholar IDs"):      
        article_data = search_google_scholar(title, iteration)
        if article_data:
            initial_pubs.append(article_data)
            seen_titles.append((title, article_data.id))
        
        if i < len(titles):
            time.sleep(delay)
    
    db_manager.insert_iteration_data(initial_pubs)
    db_manager.insert_seen_titles_data(seen_titles)


def main():
    parser = argparse.ArgumentParser(description='Generate snowball sampling starting points from file')
    parser.add_argument('--input_file', help='Path to the input file (json or text)', default=search_conf["initial_file"])
    parser.add_argument('--delay', type=float, default=2.0, 
                       help='Delay between Google Scholar requests in seconds (default: 2.0)')
    parser.add_argument('--db_path', help='db path', type=str, default=search_conf["db_path"])
    args = parser.parse_args()

    db_manager = initialize_db(args.db_path)
    generate_snowball_start(args.input_file, ITERATION_0, args.delay, db_manager)


if __name__ == "__main__":
    main()
