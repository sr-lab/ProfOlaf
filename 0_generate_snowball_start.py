#!/usr/bin/env python3
"""
Script to generate snowball sampling starting points from accepted papers.
Extracts titles from accepted_papers.json, searches Google Scholar for citation numbers,
and outputs in the format of initial.json.
"""

import argparse
import hashlib
import json
import re
import requests
import time

from dotenv import load_dotenv
from enum import Enum

from scholarly import scholarly
from tqdm import tqdm
from typing import List, Dict, Optional
from urllib.parse import quote_plus
from utils.proxy_generator import get_proxy


from utils.db_management import (
    DBManager, 
    initialize_db, 
    SelectionStage
)
from utils.pipeline.generate_snowball_start_utils import (
    generate_snowball_start,
    extract_titles_from_file
)  

from utils.article_search.article_search_method import SearchMethod

ITERATION_0 = 0 

load_dotenv()
with open("confs/search_conf.json", "r") as f:
    search_conf = json.load(f)

pg = get_proxy(search_conf["proxy_key"])


def parse_args():
    parser = argparse.ArgumentParser(description='Generate snowball sampling starting points from file')
    parser.add_argument('--input_file', help='Path to the input file (json or text)', default=search_conf["initial_file"])
    default_delay = search_conf.get("request_delay")
    if default_delay is None:
        default_delay = 2.5 if search_conf.get("search_method") == "semantic_scholar" else 1.0
    parser.add_argument('--delay', type=float, default=default_delay, help='Delay between API requests in seconds')
    parser.add_argument('--db_path', help='db path', type=str, default=search_conf["db_path"])
    parser.add_argument(
        '--search_method', 
        help='Search method to use', 
        type=str, 
        default=search_conf["search_method"],
        choices=[method.value for method in SearchMethod]
    )
    args = parser.parse_args()
    return args

def main():
    args = parse_args()

    try:
        search_method = SearchMethod(args.search_method)
    except ValueError:
        print(f"Error: Invalid search method '{args.search_method}'. Available options: {[method.value for method in SearchMethod]}")
        return

    db_manager = initialize_db(args.db_path, search_conf)
    initial_pubs, seen_titles = generate_snowball_start(args.input_file, ITERATION_0, args.delay, search_method)
    db_manager.insert_iteration_data(initial_pubs)
    db_manager.insert_seen_titles_data(seen_titles)
    db_manager.cursor.close()
    db_manager.conn.close()

if __name__ == "__main__":
    main()
