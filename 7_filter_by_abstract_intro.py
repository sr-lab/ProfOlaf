import argparse
from utils.db_management import initialize_db


def choose_elements(articles, db_manager, iteration):
    updated_data = []
    for i, article in enumerate(articles):
        if article.abstract_filtered_out:
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
                break
            elif user_input == 'n':
                article.abstract_filtered_out = True
                article.selected = False
                updated_data.append((article.id, article.abstract_filtered_out, "abstract_filtered_out"))
                updated_data.append((article.id, article.selected, "selected"))
                break
            elif user_input == 's':
                break
            else:
                print("Please enter 'y' for yes or 'n' for no.")

    db_manager.update_batch_iteration_data(iteration, updated_data)


def main(iteration, db_path):
    db_manager = initialize_db(db_path, iteration)
    articles = db_manager.get_iteration_data(iteration, selected=True)
    choose_elements(articles, db_manager, iteration)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Filter by title')
    parser.add_argument('--iteration', help='iteration number', type=int)
    parser.add_argument('--db_path', help='db path', type=str)
    args = parser.parse_args()
    main(args.iteration, args.db_path)