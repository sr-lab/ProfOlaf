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

#pg = get_proxy()

def search_google_scholar_citedby_id(title: str) -> Optional[int]:
    """
    Search Google Scholar for a paper title and extract the Google Scholar ID.
    Args:
        title: The paper title to search for
        
    Returns:
        The Google Scholar ID if found, None otherwise
    """
    try:
        search_query = scholarly.search_pubs(title)
        result = next(search_query, None)
        if result is None:
            return None
        
        scholar_id = result.get('citedby_url')
        if scholar_id is None:
            # create hash from title (the hash should be around 20 characters)
            hash_id = hashlib.md5(title.encode('utf-8')).hexdigest()
            return hash_id
        
        match = re.search(r"cites=(\d+)", scholar_id)
        if match is None:
            return None
        return int(match.group(1))
    
    except Exception as e:
        print(f"Error searching for '{title}': {e}")
        return None


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

def extract_titles_from_file(file_path: str) -> List[str]:
    """
    Extract titles from a file. The file should be a text file with one title per line.
    """
    with open(file_path, 'r', encoding='utf-8') as f:
        return [line.strip() for line in f.readlines()]

def generate_snowball_start(input_file: str, output_file: str, delay: float = 2.0):
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
        citedby_id = search_google_scholar_citedby_id(title)

        if citedby_id:
            initial_pubs.append({citedby_id: title})
            seen_titles.append(title)
        
        if i < len(titles):
            time.sleep(delay)

    output_data = {
        "initial_pubs": initial_pubs,
        "seen_titles": seen_titles,
        "new_pubs": []
    }
    
    try:
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(output_data, f, indent=2)
        
        print(f"Found Google Scholar IDs for {len(initial_pubs)} out of {len(titles)} papers")
        print(f"\nResults saved to {output_file}")

    except Exception as e:
        print(f"Error writing output file: {e}")


def main():
    parser = argparse.ArgumentParser(description='Generate snowball sampling starting points from file')
    parser.add_argument('input_file', help='Path to the input file (json or text)')
    #parser.add_argument('output_file', help='Path to the output JSON file')
    parser.add_argument('--delay', type=float, default=2.0, 
                       help='Delay between Google Scholar requests in seconds (default: 2.0)')
    
    args = parser.parse_args()
    
    generate_snowball_start(args.input_file, "test_files/iteration_0.json", args.delay)


if __name__ == "__main__":
    main()
