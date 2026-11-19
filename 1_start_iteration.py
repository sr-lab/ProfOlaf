import argparse
import json
import re
import time
import sys
from scholarly import scholarly
from dotenv import load_dotenv
import hashlib

from tqdm import tqdm

from utils.proxy_generator import get_proxy
from utils.db_management import (
    DBManager, 
    SelectionStage
)
from utils.article_search.article_search_method import (
    ArticleSearch, 
    SearchMethod,
)

from utils.pipeline.start_iteration_utils import get_articles, repair_references, SNOWBALL_DIRECTIONS


load_dotenv()
with open("confs/search_conf.json", "r") as f:
    search_conf = json.load(f)

def parse_args():
    parser = argparse.ArgumentParser(description='Generate snowball sampling starting points from file')
    parser.add_argument('--iteration', help='iteration number', type=int, required=True)
    parser.add_argument('--db_path', help='db path', type=str, default=search_conf["db_path"])
    parser.add_argument(
        '--search_method', 
        help='Search method to use', 
        type=str, 
        default=search_conf["search_method"],
        choices=[method.value for method in SearchMethod]
    )
    parser.add_argument(
        '--repair', 
        help='repair method for broken references', 
        type=str,
        default="",
        choices=["remove", "manual"])
    parser.add_argument(
        '--snowball_direction',
        help='Snowballing direction: forward (citations only), backward (references only), or both',
        type=str,
        default=search_conf.get("snowball_direction", "both"),
        choices=list(SNOWBALL_DIRECTIONS),
    )
    parser.add_argument('--verbose', action='store_true')  
    args = parser.parse_args()
    return args

if __name__ == "__main__":
    args = parse_args()
    db_manager = DBManager(args.db_path)
    

    initial_pubs = db_manager.get_iteration_data(
        iteration=args.iteration - 1, 
        selected=SelectionStage.CONTENT_APPROVED,
        search_method=args.search_method
    )

    if len(initial_pubs) == 0:
        print("No initial pubs found")
        print("Possible reasons:")
        print("1. No initial pubs found for the given search method:", args.search_method)
        print("2. No initial pubs found for the given iteration:", args.iteration)
        sys.exit(1)
    
    print("Initial Pubs: ", len(initial_pubs))
    sys.stdout.flush()
    search_method_instance = SearchMethod(args.search_method).create_instance()
    article_search = ArticleSearch(search_method_instance)
    get_articles(args.iteration, initial_pubs, db_manager, article_search, args.verbose, args.snowball_direction)
    
    if args.repair == "manual":
        repair_references(args.iteration, db_manager, args.verbose)
    elif args.repair == "remove":
        db_manager.clear_unidentified_articles(args.iteration)
    else:
        print("Invalid repair method:", args.repair)
        sys.exit(1)
