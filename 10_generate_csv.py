import argparse
import os
import re
import json
import pandas as pd
from utils.db_management import DBManager, SelectionStage
from typing import List

with open("search_conf.json", "r") as f:
    search_conf = json.load(f)

def generate_csv(db_manager: DBManager, iterations: List[int], output_path: str):
    """
    Generate a CSV file with the article data.
    """
    article_data = []
    for iteration in iterations:
        print("Iteration: ", iteration)
        articles = db_manager.get_iteration_data(iteration=iteration, selected=SelectionStage.CONTENT_APPROVED)
        print(articles)
        for article in articles:
            print(article.title)
            article_data.append({
                "title": article.title,
                "authors": article.authors,
                "year": article.pub_year,
                "venue": article.venue,
                "citations": article.num_citations,
                "url": article.eprint_url,
                "iteration": iteration,
            })
    article_data = pd.DataFrame(article_data)
    article_data.to_csv(output_path)
    

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Generate CSV')
    parser.add_argument('--iterations', help='iterations', type=int, nargs='+')
    parser.add_argument('--db_path', help='db path', type=str, default=search_conf["db_path"])
    parser.add_argument('--output_path', help='output path', type=str, default=search_conf["csv_path"])
    args = parser.parse_args()
    print(args.iterations)
    db_manager = DBManager(args.db_path)
    generate_csv(db_manager, args.iterations, args.output_path)