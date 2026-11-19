from tqdm import tqdm
import sys

from ..db_management import DBManager, build_citation_edge
from ..article_search.article_search_method import ArticleSearch, SemanticScholarSearchMethod, GoogleScholarSearchMethod, DBLPSearchMethod

SNOWBALL_DIRECTIONS = ("forward", "backward", "both")


def snowball_direction_kwargs(snowball_direction: str) -> dict:
    """
    Map user-facing snowball direction to provider kwargs.

    Forward snowballing = citations (papers that cite the seed).
    Backward snowballing = references (papers the seed cites).

    Note: provider kwargs use inverted names (backwards=citations, forwards=references).
    """
    if snowball_direction not in SNOWBALL_DIRECTIONS:
        raise ValueError(f"Invalid snowball direction: {snowball_direction}. Must be one of {SNOWBALL_DIRECTIONS}")
    return {
        "backwards": snowball_direction in ("forward", "both"),
        "forwards": snowball_direction in ("backward", "both"),
    }


def get_articles(iteration: int, initial_pubs, db_manager: DBManager, article_search: ArticleSearch, verbose: bool = False, snowball_direction: str = "both"):
    """
    Get articles from snowballing for the given iteration.
    """
    direction_kwargs = snowball_direction_kwargs(snowball_direction)

    for initial_pub in tqdm(initial_pubs, desc="Getting articles from snowballing..."):
        citedby = initial_pub.id

        # Each result is an (ArticleData, direction) tuple where direction is
        # 'citation' or 'reference'.
        results = article_search.get_snowballing_articles(citedby, iteration=iteration, **direction_kwargs)
        if len(results) == 0:
            continue

        if verbose:
            print("Citedby: ", citedby, "Total Results: ", len(results))

        sys.stdout.flush()

        # Record canonical citation edges (citing -> cited) for every discovered
        # article, including duplicates that are not re-inserted as nodes, so the
        # citation graph keeps all links. build_citation_edge orients each result
        # and carries the endpoint DOIs + source provider.
        edges = [
            build_citation_edge(initial_pub, article, direction, iteration)
            for article, direction in results
            if initial_pub.id and article.id
        ]
        db_manager.insert_citation_edges(edges)

        filtered_articles = [
            article for article, _direction in results
            if db_manager.get_seen_title(article.title) is None
        ]
        if verbose:
            print(f"Found {len(filtered_articles)} new articles from {initial_pub.title}")

        db_manager.insert_iteration_data(filtered_articles)
        db_manager.insert_seen_titles_data([(article.title, article.id) for article in filtered_articles])

    sys.stdout.flush()

def repair_references(iteration: int, db_manager: DBManager, verbose: bool = False):
    """ Repair broken references for the given iteration """
    articles = db_manager.get_iteration_data(iteration=iteration, id__empty=True)
    semantic_scholar_search = SemanticScholarSearchMethod()
    google_scholar_search = GoogleScholarSearchMethod()
    dblp_search = DBLPSearchMethod()
    search_methods = [semantic_scholar_search, google_scholar_search, dblp_search]
    for article in articles:
        confirmation = input(f"Is this an article {article.title}? (y/n):")
        if confirmation == "y":
            title = input(f"Enter the title of the article (leave empty to use the current title):")
            title = title.strip() if title.strip() != "" else article.title
            article.title = title  # Update title if changed
            for search_method in search_methods:
                found_article = search_method.search(title)
                if found_article:
                    article.id = found_article.id
                    break
            if article.id is None or article.id == "":
                print(f"No article found for {title}")
                continue
            db_manager.insert_iteration_data([article])
            # Update seen_titles table to link title to new ID
            db_manager.insert_seen_titles_data([(article.title.lower(), article.id)])
            
            # Delete the old entry (with empty ID)
            db_manager.cursor.execute(
                "DELETE FROM iterations WHERE id = ? AND iteration = ? AND title = ?",
                ('', iteration, article.title)
            )
            db_manager.conn.commit()

    db_manager.clear_unidentified_articles(iteration)