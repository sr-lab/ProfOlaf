import argparse
from utils.db_management import DBManager, SelectionStage
import json

with open("search_conf.json", "r") as f:
    search_conf = json.load(f)

def choose_elements(articles, db_manager, iteration):
    """
    Choose the elements by abstract and introduction.
    """
    updated_data = []
    for i, article in enumerate(articles):
        if article.selected >= SelectionStage.ABSTRACT_INTRO_APPROVED.value or article.abstract_filtered_out == True:
            continue

        title = article.title
        url = article.pub_url
        preprint = article.eprint_url

        print(f"({i+1}/{len(articles)})")
        while True:
            print(f"\nTitle: {title}")
            print(f"ID: {article.id}")
            print(f"Url: {url}")
            print(f"Preprint: {preprint}")

            user_input = input(f"Do you want to keep this element? (y/n/s for skip): ").strip().lower()
            if user_input == 'y':
                article.selected = SelectionStage.ABSTRACT_INTRO_APPROVED   
                updated_data.append((article.id, article.selected, "selected"))
                break
            elif user_input == 'n':
                article.abstract_filtered_out = True
                updated_data.append((article.id, article.abstract_filtered_out, "abstract_filtered_out"))
                break
            elif user_input == 's':
                break
            else:
                print("Please enter 'y' for yes or 'n' for no.")

    db_manager.update_batch_iteration_data(iteration, updated_data)


def main(iteration, db_path):
    db_manager = DBManager(db_path)
    articles = db_manager.get_iteration_data(
        iteration=iteration,
        selected=SelectionStage.TITLE_APPROVED,
    )
    choose_elements(articles, db_manager, iteration)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Filter by title')
    parser.add_argument('--iteration', help='iteration number', type=int, required=True)
    parser.add_argument('--db_path', help='db path', type=str, default=search_conf["db_path"])
    args = parser.parse_args()
    main(args.iteration, args.db_path)