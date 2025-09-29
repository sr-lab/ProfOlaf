import argparse
import json
from typing import List, Tuple, Dict
from difflib import SequenceMatcher
import sys

from utils.db_management import DBManager, SelectionStage

with open("search_conf.json", "r") as f:
    search_conf = json.load(f)


def parse_args():
    parser = argparse.ArgumentParser(description='Remove duplicate articles')
    parser.add_argument('--db_path', help='db path', type=str, default=search_conf["db_path"])
    parser.add_argument('--iterations', help='iterations', type=int, nargs='+')
    parser.add_argument('--similarity_threshold', help='Title similarity threshold (0.0-1.0)', 
                       type=float, default=0.8)
    parser.add_argument('--auto_remove', help='Automatically remove duplicates without user confirmation', 
                       action='store_true')
    return parser.parse_args()


def calculate_title_similarity(title1: str, title2: str) -> float:
    """
    Calculate similarity between two titles using SequenceMatcher.
    Returns a float between 0.0 and 1.0.
    """
    norm_title1 = title1.lower().strip()
    norm_title2 = title2.lower().strip()
    
    return SequenceMatcher(None, norm_title1, norm_title2).ratio()


def find_duplicate_candidates(articles: List, similarity_threshold: float) -> List[Tuple]:
    """
    Find potential duplicate articles based on title similarity.
    Returns a list of tuples containing (article1, article2, similarity_score).
    """
    duplicates = []
    processed = set()
    
    for i, article1 in enumerate(articles):
        if article1.id in processed:
            continue
            
        for j, article2 in enumerate(articles[i+1:], i+1):
            if article2.id in processed:
                continue
                
            similarity = calculate_title_similarity(article1.title, article2.title)
            
            if similarity >= similarity_threshold:
                duplicates.append((article1, article2, similarity))
                processed.add(article1.id)
                processed.add(article2.id)
                break  
    
    return duplicates


def display_article_info(article, index: int = None):
    """Display article information in a readable format."""
    prefix = f"[{index}] " if index is not None else ""
    print(f"{prefix}ID: {article.id}")
    print(f"{prefix}Title: {article.title}")
    print(f"{prefix}Authors: {article.authors}")
    print(f"{prefix}Venue: {article.venue}")
    print(f"{prefix}Year: {article.pub_year}")
    print(f"{prefix}URL: {article.pub_url}")
    print("-" * 80)


def resolve_duplicate_interactive(article1, article2, similarity: float) -> str:
    """
    Interactive resolution of duplicate articles.
    Returns the ID of the article to keep, or 'both' to keep both.
    """
    print(f"\n{'='*80}")
    print(f"POTENTIAL DUPLICATE DETECTED (Similarity: {similarity:.3f})")
    print(f"{'='*80}")
    
    print("\nArticle 1:")
    display_article_info(article1, 1)
    
    print("\nArticle 2:")
    display_article_info(article2, 2)
    
    while True:
        print("\nOptions:")
        print("1 - Keep Article 1 (remove Article 2)")
        print("2 - Keep Article 2 (remove Article 1)")
        print("3 - Keep both articles (not duplicates)")
        print("q - Quit without resolving")
        
        choice = input("\nEnter your choice (1/2/3/q): ").strip().lower()
        
        if choice == '1':
            return article1.id
        elif choice == '2':
            return article2.id
        elif choice == '3':
            return 'both'
        elif choice == 'q':
            return 'quit'
        else:
            print("Invalid choice. Please enter 1, 2, 3, or q.")


def remove_duplicates(db_manager: DBManager, iterations: List[int], 
                     similarity_threshold: float, auto_remove: bool = False):
    """
    Main function to detect and remove duplicate articles.
    """
    print(f"Fetching articles from iterations: {iterations}")
    total_articles = []
    for iteration in iterations:
        articles = db_manager.get_iteration_data(iteration=iteration, selected=SelectionStage.CONTENT_APPROVED)
        total_articles.extend(articles)
        print(f"  Iteration {iteration}: {len(articles)} articles")
    
    print(f"\nTotal articles to check: {len(total_articles)}")
    
    print(f"\nSearching for duplicates with similarity threshold: {similarity_threshold}")
    duplicates = find_duplicate_candidates(total_articles, similarity_threshold)
    
    if not duplicates:
        print("No potential duplicates found!")
        return
    
    print(f"Found {len(duplicates)} potential duplicate pairs")
    
    articles_to_remove = set()
    articles_to_keep = set()
    
    for i, (article1, article2, similarity) in enumerate(duplicates, 1):
        print(f"\nProcessing duplicate pair {i}/{len(duplicates)}")
        
        if auto_remove:
            if article1.num_citations > article2.num_citations:
                keep_id, remove_id = article1.id, article2.id
            elif article2.num_citations > article1.num_citations:
                keep_id, remove_id = article2.id, article1.id
            elif article1.pub_year > article2.pub_year:
                keep_id, remove_id = article1.id, article2.id
            else:
                keep_id, remove_id = article2.id, article1.id
            
            print(f"Auto-removing: Keeping {keep_id}, removing {remove_id}")
            articles_to_keep.add(keep_id)
            articles_to_remove.add(remove_id)
        else:
            decision = resolve_duplicate_interactive(article1, article2, similarity)
            
            if decision == 'quit':
                print("Exiting without completing duplicate removal.")
                return
            elif decision == 'both':
                articles_to_keep.add(article1.id)
                articles_to_keep.add(article2.id)
                print("Both articles will be kept.")
            else:
                other_id = article2.id if decision == article1.id else article1.id
                articles_to_keep.add(decision)
                articles_to_remove.add(other_id)
                print(f"Will keep article {decision}, remove article {other_id}")
    
    if articles_to_remove:
        print(f"\nUpdating database: marking {len(articles_to_remove)} articles as duplicates")
        
        article_map = {article.id: article for article in total_articles}
        
        for article_id in articles_to_remove:
            article = article_map[article_id]
            db_manager.update_iteration_data(
                iteration=article.iteration, 
                article_id=article_id, 
                selected=SelectionStage.DUPLICATE
            )
        print("Database updated successfully!")
    else:
        print("No articles were marked as duplicates.")


if __name__ == "__main__":
    args = parse_args()
    db_manager = DBManager(args.db_path)
    remove_duplicates(db_manager, args.iterations, args.similarity_threshold, args.auto_remove)

