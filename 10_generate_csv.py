import os
import re
import sys
import json
import pandas as pd


def get_iteration(initial_iteration_file):
    if re.match(r"initial-iteration-(\d+).json", initial_iteration_file):
        return int(re.match(r"initial-iteration-(\d+).json", initial_iteration_file).group(1))
    raise RuntimeError(f"Invalid initial iteration file: {initial_iteration_file}")


if __name__ == "__main__":
    initials_folder = sys.argv[1]
    articles = []
    for file in os.listdir(initials_folder):
        if file.startswith("initial-") and file.endswith(".json"):
            with open(file, 'r') as f:
                selected_articles = set(json.load(f)['initial_pubs'])
                iteration = get_iteration(file)
                
                for selected_article in selected_articles:
                    with open(
                        os.path.join("articles", str(iteration), selected_article, "info.json"), "r"
                    ) as info:
                        article_info = json.load(info)
                        url = article_info["pub_url"]
                        eprint_url = article_info["eprint_url"] if "eprint_url" in article_info else ""
                        articles.append(
                            {
                                "title": article_info["bib"]["title"],
                                "year": article_info["bib"]["pub_year"],
                                "authors": ", ".join(article_info["bib"]["author"]),
                                "citations": article_info["num_citations"],
                                "url": url,
                                "eprint_url": eprint_url,
                            }
                        )

    articles = pd.DataFrame(articles)
    articles.to_csv("articles.csv")