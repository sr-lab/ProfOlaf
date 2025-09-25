import argparse
import json
from utils.db_management import DBManager, SelectionStage

with open("search_conf.json", "r") as f:
    search_conf = json.load(f)

def choose_elements(articles, db_manager, iteration): 
    """
    Choose the elements by title.
    """
    updated_data = []
    for i, article in enumerate(articles):
        if article.selected >= SelectionStage.TITLE_APPROVED.value or article.title_filtered_out == True:
            print(f"\n({i+1}/{len(articles)}) Skipping Article {article.title}. Selected: {article.selected}, Title Filtered Out: {article.title_filtered_out}")
            continue
        print(f"\n({i+1}/{len(articles)})")
        while True:
            print(f"Title: {article.title}")
            print(f"ID: {article.id}")
            user_input = input(f"Do you want to keep this element? (y/n/s for skip): ").strip().lower()
            if user_input == 'y':
                user_reason = input(f"Please enter the reason for the selection (enter to skip): ").strip()
                article.selected = SelectionStage.TITLE_APPROVED
                updated_data.append((article.id, article.selected, "selected"))
                updated_data.append((article.id, user_reason, "title_reason"))
                break
            elif user_input == 'n':
                user_reason = input(f"Please enter the reason for the rejection (enter to skip): ").strip()
                article.title_filtered_out = True
                updated_data.append((article.id, article.title_filtered_out, "title_filtered_out"))
                updated_data.append((article.id, user_reason, "title_reason"))
                break
            elif user_input == 's':
                break
            else:
                print("Please enter 'y' for yes or 'n' for no.")

        db_manager.update_batch_iteration_data(iteration, updated_data)
        
def main(iteration, db_path, refresh):
    db_manager = DBManager(db_path)
    articles = db_manager.get_iteration_data(
        iteration=iteration, 
        selected=SelectionStage.METADATA_APPROVED
        )
    
    if refresh:
        print("TODO: Refresh the database at iteration", iteration, "for the title check")
  
    choose_elements(articles, db_manager, iteration)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Filter by title')
    parser.add_argument('--iteration', help='iteration number', type=int, required=True)
    parser.add_argument('--db_path', help='db path', type=str, default=search_conf["db_path"])
    parser.add_argument('--refresh', help='refresh the database', action='store_true')
    args = parser.parse_args()
    main(args.iteration, args.db_path, args.refresh)