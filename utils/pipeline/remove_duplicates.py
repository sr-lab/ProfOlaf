import json
from collections import defaultdict
from typing import List, Tuple, Dict
from difflib import SequenceMatcher
import sys

from ..db_management import DBManager, SelectionStage
from ..article_search.article_search_method import normalize_doi

with open("confs/search_conf.json", "r") as f:
    search_conf = json.load(f)


def _to_int(value, default: int = 0) -> int:
    """Best-effort integer parse. num_citations and pub_year are stored as TEXT
    (and read back as strings), so comparisons must cast them to int first,
    otherwise `>` compares lexicographically (e.g. "100" < "9")."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def calculate_title_similarity(title1: str, title2: str) -> float:
    """
    Calculate similarity between two titles using SequenceMatcher.
    Returns a float between 0.0 and 1.0.
    """
    norm_title1 = title1.lower().strip()
    norm_title2 = title2.lower().strip()
    
    return SequenceMatcher(None, norm_title1, norm_title2).ratio()

def find_duplicate_clusters(articles: List, similarity_threshold: float) -> List[List]:
    """
    Group articles into duplicate clusters.

    Two articles are considered the same work when they share a (normalized) DOI
    — the most reliable signal, catching the same paper published under different
    titles (e.g. preprint vs. conference version) — or when their titles are at
    least ``similarity_threshold`` similar. Clustering is transitive (union-find),
    so three or more near-identical papers collapse into a single cluster in one
    pass instead of leaving leftovers for a later run.

    Returns a list of clusters (each a list of ArticleData); only clusters with
    more than one article are returned.
    """
    n = len(articles)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    # 1) Deterministic DOI matches.
    doi_first_index: Dict[str, int] = {}
    for i, article in enumerate(articles):
        doi = normalize_doi(getattr(article, "doi", "") or "")
        if not doi:
            continue
        if doi in doi_first_index:
            union(i, doi_first_index[doi])
        else:
            doi_first_index[doi] = i

    # 2) Title-similarity matches (skip pairs already clustered via DOI).
    for i in range(n):
        for j in range(i + 1, n):
            if find(i) == find(j):
                continue
            if calculate_title_similarity(articles[i].title, articles[j].title) >= similarity_threshold:
                union(i, j)

    clusters: Dict[int, List] = defaultdict(list)
    for i in range(n):
        clusters[find(i)].append(articles[i])
    return [members for members in clusters.values() if len(members) > 1]


def pick_best_article(cluster: List):
    """Choose which article in a duplicate cluster to keep: most citations, then
    most recent year."""
    return max(
        cluster,
        key=lambda a: (_to_int(a.num_citations, -1), _to_int(a.pub_year, 0)),
    )

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


def resolve_duplicate_cluster_interactive(cluster: List) -> str:
    """
    Interactive resolution of a duplicate cluster (two or more articles).
    Returns the ID of the article to keep, 'both' to keep all of them, or 'quit'.
    """
    print(f"\n{'='*80}")
    print(f"POTENTIAL DUPLICATE CLUSTER DETECTED ({len(cluster)} articles)")
    print(f"{'='*80}")

    for idx, article in enumerate(cluster, 1):
        print(f"\nArticle {idx}:")
        display_article_info(article, idx)

    while True:
        print("\nOptions:")
        print(f"1-{len(cluster)} - Keep that article (remove the others)")
        print("a - Keep all articles (not duplicates)")
        print("q - Quit without resolving")

        choice = input(f"\nEnter your choice (1-{len(cluster)}/a/q): ").strip().lower()

        if choice == 'a':
            return 'both'
        if choice == 'q':
            return 'quit'
        if choice.isdigit() and 1 <= int(choice) <= len(cluster):
            return cluster[int(choice) - 1].id
        print(f"Invalid choice. Please enter 1-{len(cluster)}, a, or q.")


def remove_duplicates(db_manager: DBManager, iterations: List[int], 
                     similarity_threshold: float, auto_remove: bool = False):
    """
    Main function to detect and remove duplicate articles.
    """
    print(f"Fetching articles from iterations: {iterations}")
    total_articles = []
    for iteration in iterations:
        articles = db_manager.get_iteration_data(iteration=iteration)
        total_articles.extend(articles)
        print(f"  Iteration {iteration}: {len(articles)} articles")
    
    print(f"\nTotal articles to check: {len(total_articles)}")
    
    print(f"\nSearching for duplicates with similarity threshold: {similarity_threshold} (plus exact DOI matches)")
    clusters = find_duplicate_clusters(total_articles, similarity_threshold)

    if not clusters:
        print("No potential duplicates found!")
        return

    print(f"Found {len(clusters)} potential duplicate cluster(s)")

    articles_to_remove = set()
    articles_to_keep = set()

    for i, cluster in enumerate(clusters, 1):
        print(f"\nProcessing duplicate cluster {i}/{len(clusters)} ({len(cluster)} articles)")

        if auto_remove:
            best = pick_best_article(cluster)
            print(f"Auto-removing: Keeping {best.id}, removing {len(cluster) - 1} other(s)")
            for article in cluster:
                if article.id == best.id:
                    articles_to_keep.add(article.id)
                else:
                    articles_to_remove.add(article.id)
        else:
            decision = resolve_duplicate_cluster_interactive(cluster)

            if decision == 'quit':
                print("Exiting without completing duplicate removal.")
                return
            elif decision == 'both':
                for article in cluster:
                    articles_to_keep.add(article.id)
                print("All articles in the cluster will be kept.")
            else:
                for article in cluster:
                    if article.id == decision:
                        articles_to_keep.add(article.id)
                    else:
                        articles_to_remove.add(article.id)
                print(f"Will keep article {decision}, remove the other {len(cluster) - 1}")

    # An id kept in one cluster must never be removed because of another.
    articles_to_remove -= articles_to_keep

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

