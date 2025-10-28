import argparse
import json
import csv
from typing import List
from utils.db_management import DBManager, SelectionStage

with open("search_conf.json", "r") as f:
    search_conf = json.load(f)

def extract_reasonings(db_manager: DBManager, iterations: List[int], output_path: str):
    """
    Extract the reasonings from the database.
    """
    with open(output_path, "w") as f:
        writer = csv.writer(f)
        writer.writerow(["title", "title_reason", "content_reason"])
        for iteration in iterations:
            articles = db_manager.get_iteration_data(iteration=iteration, selected=SelectionStage.CONTENT_APPROVED)
            for article in articles:
                row = [article.title]
                if isinstance(article.title_reason, dict):
                    for rater in article.title_reason:
                        row.append(f"{rater}: {article.title_reason[rater]}")
                else:
                    row.append(article.title_reason)
                if isinstance(article.content_reason, dict):
                    for rater in article.content_reason:
                        row.append(f"{rater}: {article.content_reason[rater]}")
                else:
                    row.append(article.content_reason)
                writer.writerow(row)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Extract the reasonings from the database.')
    parser.add_argument('--db_path', help='db path', type=str, default=search_conf["db_path"])
    parser.add_argument('--iterations', help='iterations', type=int, nargs='+')
    parser.add_argument('--output_path', help='output path', type=str, default="reasonings.csv")
    args = parser.parse_args()
    db_manager = DBManager(args.db_path)
    extract_reasonings(db_manager, args.iterations)