import time
from ..db_management import DBManager, SelectionStage
from ..article_search.article_search_method import SearchMethod, ArticleSearch
from tqdm import tqdm 
from typing import List, Optional, Callable

def extract_titles_from_file(file_path: str) -> List[str]:
    """
    Extract titles from a file. The file should be a text file with one title per line.
    """
    with open(file_path, 'r', encoding='utf-8') as f:
        return [line.strip() for line in f.readlines() if line.strip()]  

def generate_snowball_start(
    input_file: str, 
    iteration: int, 
    delay: float = 2.0, 
    search_method: SearchMethod = SearchMethod.GOOGLE_SCHOLAR,
    progress_callback: Optional[Callable[[int, int], None]] = None
    ):
    """
    Generate snowball sampling starting points from accepted papers.
    
    Args:
        input_file: Path to the input JSON file (e.g., accepted_papers.json)
        iteration: Iteration number for the search
        delay: Delay between requests to avoid rate limiting
        search_method: Search method to use (SearchMethod enum)
    """
    print(f"Reading titles from {input_file}...")
    titles = extract_titles_from_file(input_file)
    print(f"Titles: {titles}")
    if not titles:
        print("No titles found in the input file.")
        return
    print(f"Found {len(titles)} titles. Starting searches with {search_method.value}...")
    
    # Set total for progress tracking
    if progress_callback:
        progress_callback(0, len(titles))
    
    search_method_instance = search_method.create_instance()
    article_search = ArticleSearch(search_method_instance)
    
    initial_pubs = []
    seen_titles = []
    for i, title in tqdm(enumerate(titles, 1), total=len(titles), desc=f"Searching with {search_method.value}"):      
        try:
            article_data = article_search.search(title)
        except Exception as e:
            print(f"Error searching for '{title}': {e}")
            article_data = None
        if article_data:
            article_data.set_iteration(iteration)
            article_data.set_selected(SelectionStage.CONTENT_APPROVED)
            initial_pubs.append(article_data)
            seen_titles.append((title, article_data.id))

        # Update progress
        if progress_callback:
            progress_callback(i, len(titles))

        if i < len(titles):
            time.sleep(delay)

    return initial_pubs, seen_titles
