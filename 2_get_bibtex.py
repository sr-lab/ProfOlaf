import os
import time
import sys
import json
from scholarly import scholarly
from scholarly import ProxyGenerator
from dotenv import load_dotenv
import argparse
from utils.db_management import (
    DBManager, 
    ArticleData,
    SelectionStage,
)

from tqdm import tqdm
from utils.proxy_generator import get_proxy
import bibtexparser
import concurrent.futures
from functools import lru_cache
from typing import List, Tuple, Optional
import requests
from utils.article_search_method import (
    ArticleSearch, 
    GoogleScholarSearchMethod, 
    DBLPSearchMethod,
)

load_dotenv()
with open("search_conf.json", "r") as f:
    search_conf = json.load(f)
pg = get_proxy(search_conf["proxy_key"])

def check_valid_venue(venue: str):
    return venue != "" and "arxiv" not in venue.lower() \
        and "corr" not in venue.lower() \
        and "no title" not in venue.lower()


def search_bibtex_in_dblp(title: str):
    dblp_url = f"https://dblp.org/search/publ/api?q={title}&format=json"
    response = requests.get(dblp_url)
    data = response.json()
    if data.get("result", {}).get("hits", {}).get("hit", []) != []:
        for hit in data["result"]["hits"]["hit"]:
            if hit["info"]["venue"] and check_valid_venue(hit["info"]["venue"]):
                return hit["info"]
    return ""

def cache_by_title(func):
    cache = {}
    def wrapper(pub):
        title = pub.get("bib", {}).get("title", "")
        if title and title not in cache:
            cache[title] = func(pub)
        return cache.get(title, "")
    return wrapper

@cache_by_title
def get_alternative_bibtexes_cached(pub):
    """
    Cached version of get_alternative_bibtexes to avoid repeated API calls.
    Uses pub as cache key.
    """

    article_search = ArticleSearch(GoogleScholarSearchMethod())
    versions = article_search.get_all_versions_bibtexes(pub)
    for version in versions:
        venue = get_bibtex_venue(version)
        if check_valid_venue(venue):
            return version
    return ""


def get_bibtex_venue(bibtex: str):
    if bibtex != "":
        library = bibtexparser.loads(bibtex)
        # Check if entries list is not empty before accessing first element
        if not library.entries:
            return ""
        if library.entries[0]["ENTRYTYPE"] in ["book", "phdthesis", "mastersthesis"]:
            return ""
        if "booktitle" in library.entries[0]:
            return library.entries[0]["booktitle"]
        elif "journal" in library.entries[0]:
            return library.entries[0]["journal"]
    
    return ""

def _get_main_bibtex(article: ArticleData) -> Tuple[str, str]:
    current_wait_time = 20
    max_retries = 3
    retry_count = 0

    article_search = ArticleSearch(GoogleScholarSearchMethod())
    while retry_count < max_retries:
        try:
            article_search.set_method(GoogleScholarSearchMethod())
            pub = scholarly.search_single_pub(article.title)
            bibtex = article_search.get_bibtex(pub)
            venue = get_bibtex_venue(bibtex)
            # Early venue check - if venue is already valid, skip alternative searches
            if venue and check_valid_venue(venue):
                return bibtex, None
            else:
                return bibtex, pub
        except Exception as e:
            title = article.title
            print(f"Error processing {title}: {e}")
            retry_count += 1
            print(f"Retrying, waiting {current_wait_time}...", file=sys.stderr)
            sys.stdout.flush()
            time.sleep(current_wait_time)
            current_wait_time *= 2
            continue
    return None, None

def _get_dblp_bibtex(article: ArticleData) -> Tuple[str, str]:
    article_search = ArticleSearch(DBLPSearchMethod())
    current_wait_time = 20
    max_retries = 3
    retry_count = 0
    while retry_count < max_retries:
        try:
            bibtex = article_search.get_bibtex(article)
            if bibtex and get_bibtex_venue(bibtex) and check_valid_venue(get_bibtex_venue(bibtex)):
                return bibtex
            else:
                return bibtex
        except Exception as e:
            title = article.title
            print(f"Error processing {title}: {e}")
            retry_count += 1
            print(f"Retrying, waiting {current_wait_time}...", file=sys.stderr)
            sys.stdout.flush()
            time.sleep(current_wait_time)
            current_wait_time *= 2
            continue

    return None

def _get_alternative_bibtex(pub: dict) -> Tuple[str, str]:
    current_wait_time = 20
    max_retries = 3
    retry_count = 0
    while retry_count < max_retries:
        try:
            bibtex = get_alternative_bibtexes_cached(pub)
            if bibtex is not None and bibtex != "":
                return bibtex
            else:
                return None
        except Exception as e:
            title = pub.get('title', "")
            print(f"Error processing {title}: {e}")
            retry_count += 1
            print(f"Retrying, waiting {current_wait_time}...", file=sys.stderr)
            sys.stdout.flush()
            time.sleep(current_wait_time)
            current_wait_time *= 2
            continue
        
    return None

def get_bibtex_single(article: ArticleData) -> Tuple[str, str]:
    """
    Get the bibtex string for a single article.
    Returns (article_id, bibtex_string)
    """

    dblp_bibtex = _get_dblp_bibtex(article)
    if dblp_bibtex is not None and dblp_bibtex != "":
        return article.id, dblp_bibtex
    
    scholar_bibtex, pub = _get_main_bibtex(article)
    if scholar_bibtex is not None and scholar_bibtex != "" and pub is None:
        return article.id, scholar_bibtex

    if pub is not None:
        alternative_bibtex = _get_alternative_bibtex(pub)
        if alternative_bibtex is not None and alternative_bibtex != "":
            return article.id, alternative_bibtex
        
    if pub is not None and scholar_bibtex is not None and scholar_bibtex != "":
        return article.id, scholar_bibtex
    else:
        print("No bibtex found")
        return article.id, "NO_BIBTEX"

def process_articles_batch(articles: List[ArticleData], max_workers: int = 3) -> List[Tuple[str, str]]:
    """
    Process multiple articles in parallel.
    Returns list of (article_id, bibtex_string) tuples.
    """
    results = []
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all tasks
        future_to_article = {
            executor.submit(get_bibtex_single, article): article 
            for article in articles
        }
        
        # Collect results as they complete
        for future in concurrent.futures.as_completed(future_to_article):
            article = future_to_article[future]
            try:
                result = future.result()
                # Ensure result is not None and is a tuple
                if result is None:
                    result = (article.id, "")
                elif not isinstance(result, tuple) or len(result) != 2:
                    result = (article.id, "")
                
                results.append(result)
            except Exception as e:
                results.append((article.id, ""))
    
    return results


def process_articles_optimized(iteration: int, articles: List[ArticleData], 
                              batch_size: int = 10, max_workers: int = 3, 
                              use_parallel: bool = True) -> None:
    """
    Optimized processing of articles with batch updates and optional parallel processing.
    """
    # Filter articles that need processing
    articles_to_process = [a for a in articles if a.bibtex == "" and a.title != ""]
    
    if not articles_to_process:
        print("No articles need processing.")
        return
    
    print(f"Processing {len(articles_to_process)} articles...")
    
    if use_parallel and len(articles_to_process) > 1:
        print(f"Using parallel processing with {max_workers} workers...")
        # Process in batches to avoid overwhelming the API
        for i in tqdm(range(0, len(articles_to_process), batch_size), desc="Getting bibtex for articles (parallel)"):
            batch = articles_to_process[i:i + batch_size]
            
            results = process_articles_batch(batch, max_workers)
            
            if results is None:
                print(f"Warning: process_articles_batch returned None for batch {i//batch_size + 1}")
                results = []
            
            update_data = [(article_id, bibtex, "bibtex") for article_id, bibtex in results]
            db_manager.update_batch_iteration_data(iteration, update_data)
            print(f"Batch {i//batch_size + 1} completed and saved to database.")
    else:
        print("Using sequential processing...")
        results = []
        desc = f"Getting bibtex for articles (sequential with batch size {batch_size})"
        for i, article in tqdm(enumerate(articles_to_process), desc=desc):
            article_id, bibtex = get_bibtex_single(article)
            results.append((article_id, bibtex, "bibtex"))
            if len(results) >= batch_size or i == len(articles_to_process) - 1:
                db_manager.update_batch_iteration_data(iteration, results)
                results = []

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Get bibtex for articles')
    parser.add_argument('--iteration', help='iteration number', type=int)
    parser.add_argument('--db_path', help='db path', type=str, default=search_conf["db_path"])
    parser.add_argument('--batch_size', help='batch size for processing', type=int, default=20)
    parser.add_argument('--max_workers', help='max workers for parallel processing', type=int, default=3)
    parser.add_argument('--parallel', help='disable parallel processing', action='store_true')
    args = parser.parse_args()

    db_manager = DBManager(args.db_path)
    articles = db_manager.get_iteration_data(
        iteration=args.iteration, 
        bibtex__empty=True,
        selected=SelectionStage.NOT_SELECTED
    )
    
    print(f"Found {len(articles)} articles without bibtex in iteration {args.iteration}")
    
    # Use optimized processing
    process_articles_optimized(
        iteration=args.iteration,
        articles=articles,
        batch_size=args.batch_size,
        max_workers=args.max_workers,
        use_parallel=args.parallel
    )
    
    print("Processing completed!")
