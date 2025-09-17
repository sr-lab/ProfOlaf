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
    get_article_data,
    initialize_db
)
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
    def wrapper(pub: scholarly.Publication):
        title = pub.get("title", "")
        if title and title not in cache:
            cache[title] = func(pub)
        return cache[title]
    return wrapper

@cache_by_title
def get_alternative_bibtexes_cached(pub: scholarly.Publication):
    """
    Cached version of get_alternative_bibtexes to avoid repeated API calls.
    Uses pub as cache key.
    """
    try:
        article_search = ArticleSearch(GoogleScholarSearchMethod())
        versions = article_search.get_all_versions(pub)
        for version in versions:
            booktitle = version.get("booktitle", "") or version.get("bib", {}).get("booktitle", "")
            journal = version.get("journal", "") or version.get("bib", {}).get("journal", "")
            venue = version.get("venue", "")
            final_venue = booktitle or journal or venue
            if check_valid_venue(final_venue):
                return version
        return ""
    except Exception as e:
        print(f"Error in get_alternative_bibtexes_cached for {pub.get("title", "")}: {e}")
        return ""

def get_bibtex_venue(bibtex: str):
    if bibtex != "":
        library = bibtexparser.loads(bibtex)
        if library.entries[0]["ENTRYTYPE"] in ["book", "phdthesis", "mastersthesis"]:
            return ""
        if "booktitle" in library.entries[0]:
            return library.entries[0]["booktitle"]
        elif "journal" in library.entries[0]:
            return library.entries[0]["journal"]
    
    return ""

def get_bibtex_single(article: ArticleData) -> Tuple[str, str]:
    """
    Get the bibtex string for a single article.
    Returns (article_id, bibtex_string)
    """
    current_wait_time = 30
    max_retries = 3
    retry_count = 0
    print(f"Processing: {article.title}")

    article_search = ArticleSearch(GoogleScholarSearchMethod())
    
    while retry_count < max_retries:
        try:
            pub = article_search.search(article.title)
            bibtex = article_search.get_bibtex(pub)
            venue = get_bibtex_venue(bibtex)
            
            # Early venue check - if venue is already valid, skip alternative searches
            if venue and check_valid_venue(venue):
                return article.id, bibtex
            
            # Only search alternatives if venue contains arxiv/Corr/No title
            alternative_bibtex = ""
            if venue and "arxiv" in venue.lower():
                article_search.set_method(DBLPSearchMethod())
                alternative_bibtex = article_search.get_bibtex(article.title)
                if alternative_bibtex:
                    return article.id, alternative_bibtex
            
                alternative_bibtex = get_alternative_bibtexes_cached(pub)
            
            final_bibtex = alternative_bibtex if alternative_bibtex != "" else bibtex
            print(f"BibTeX found for {article.title}")
            return article.id, final_bibtex
            
        except Exception as e:
            print(f"Error processing {article.title}: {e}")
            retry_count += 1
            print(f"Retrying, waiting {current_wait_time}...", file=sys.stderr)
            sys.stdout.flush()
            time.sleep(current_wait_time)
            current_wait_time *= 2
            continue
    
    # If all retries are exhausted, return empty bibtex
    print(f"Failed to get bibtex for {article.title} after {max_retries} retries")
    return article.id, ""

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
                    #print(f"Warning: get_bibtex_single returned None for {article.title}")
                    result = (article.id, "")
                elif not isinstance(result, tuple) or len(result) != 2:
                    #print(f"Warning: get_bibtex_single returned invalid result for {article.title}: {result}")
                    result = (article.id, "")
                
                results.append(result)
                #print(f"Completed: {article.title}")
            except Exception as e:
                #print(f"Error processing {article.title}: {e}")
                # Add empty result to maintain order
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
        for i in range(0, len(articles_to_process), batch_size):
            batch = articles_to_process[i:i + batch_size]
            print(f"Processing batch {i//batch_size + 1}/{(len(articles_to_process) + batch_size - 1)//batch_size}")
            
            # Process batch in parallel
            results = process_articles_batch(batch, max_workers)
            
            # Safety check - ensure results is not None
            if results is None:
                print(f"Warning: process_articles_batch returned None for batch {i//batch_size + 1}")
                results = []
            
            # Batch update database
            update_data = [(article_id, bibtex, "bibtex") for article_id, bibtex in results]
            db_manager.update_batch_iteration_data(iteration, update_data)
            print(f"Batch {i//batch_size + 1} completed and saved to database.")
    else:
        print("Using sequential processing...")
        # Sequential processing with batch updates
        results = []
        for i, article in enumerate(articles_to_process):
            print(f"Processing {i+1}/{len(articles_to_process)}: {article.title}")
            article_id, bibtex = get_bibtex_single(article)
            results.append((article_id, bibtex, "bibtex"))
            
            # Update database in batches
            if len(results) >= batch_size or i == len(articles_to_process) - 1:
                db_manager.update_batch_iteration_data(iteration, results)
                print(f"Saved batch of {len(results)} articles to database.")
                results = []

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Get bibtex for articles')
    parser.add_argument('--iteration', help='iteration number', type=int)
    parser.add_argument('--db_path', help='db path', type=str, default=search_conf["db_path"])
    parser.add_argument('--batch_size', help='batch size for processing', type=int, default=20)
    parser.add_argument('--max_workers', help='max workers for parallel processing', type=int, default=1)
    parser.add_argument('--no_parallel', help='disable parallel processing', action='store_true')
    args = parser.parse_args()

    db_manager = DBManager(args.db_path)
    articles = db_manager.get_iteration_data(iteration=args.iteration, bibtex__empty=True)
    
    print(f"Found {len(articles)} articles without bibtex in iteration {args.iteration}")
    
    # Use optimized processing
    process_articles_optimized(
        iteration=args.iteration,
        articles=articles,
        batch_size=args.batch_size,
        max_workers=args.max_workers,
        use_parallel=not args.no_parallel
    )
    
    print("Processing completed!")
