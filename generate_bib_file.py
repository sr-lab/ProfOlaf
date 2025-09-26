import argparse
import json
from typing import List

from utils.db_management import DBManager, SelectionStage

with open("search_conf.json", "r") as f:
    search_conf = json.load(f)

def generate_bib_file(db_manager: DBManager, output_path: str, iterations: List[int]):
    """
    Generate a bib file from the database.
    """
    with open(output_path, "w") as f:
        for iteration in iterations:
            print("Iteration: ", iteration)
            articles = db_manager.get_iteration_data(iteration=iteration, selected=SelectionStage.CONTENT_APPROVED)
            print("Number of articles: ", len(articles))
            for article in articles:
                f.write(article.bibtex + "\n")

    print("Bib file generated successfully")

if __name__ == "__main__":

    parser = argparse.ArgumentParser(description='Generate a bib file from the database.')
    parser.add_argument('--db_path', help='db path', type=str, default=search_conf["db_path"])
    parser.add_argument('--bib_file_path', help='bib file path', type=str, default="articles.bib")
    parser.add_argument('--iterations', help='iterations', type=int, nargs='+')
    args = parser.parse_args()
    db_manager = DBManager(args.db_path)
    generate_bib_file(db_manager, args.bib_file_path, args.iterations)