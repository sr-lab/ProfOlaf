import argparse
import json
from utils.db_management import DBManager, SelectionStage

from utils.pretty_print_utils import pretty_print, format_color_string, prompt_input


with open("search_conf.json", "r") as f:
    search_conf = json.load(f)



def choose_elements(articles, db_manager, iteration): 
    """
    Choose the elements by title.
    """
    updated_data = []
    for i, article in enumerate(articles):
        print(f"\n({i+1}/{len(articles)})")
        title_string = format_color_string(article.title, "magenta", "bold")
        if int(article.selected) >= int(SelectionStage.TITLE_APPROVED.value) or article.title_filtered_out == True:
            skip_reason = format_color_string("Article already selected", "green", "bold") if article.selected >= SelectionStage.TITLE_APPROVED.value else format_color_string("Article already filtered out", "red", "bold")
            pretty_print(f"Skipping Article {title_string}: {skip_reason}")
        while True:
            pretty_print(f"Title: {title_string}")
            user_input = prompt_input(f"Do you want to keep this element? (y/n/s for skip)").strip().lower()
            if user_input == 'y':
                user_reason = prompt_input(f"Please enter the reason for the selection (enter to skip)").strip()
                article.selected = SelectionStage.TITLE_APPROVED
                updated_data.append((article.id, article.selected, "selected"))
                updated_data.append((article.id, user_reason, "title_reason"))
                break
            elif user_input == 'n':
                user_reason = prompt_input(f"Please enter the reason for the rejection (enter to skip)").strip()
                article.title_filtered_out = True
                updated_data.append((article.id, article.title_filtered_out, "title_filtered_out"))
                updated_data.append((article.id, user_reason, "title_reason"))
                break
            elif user_input == 's':
                break
            else:
                pretty_print("Please enter 'y' for yes or 'n' for no.")

        db_manager.update_batch_iteration_data(iteration, updated_data)
        
def main(iteration, db_path, refresh):
    db_manager = DBManager(db_path)
    articles = db_manager.get_iteration_data(
        iteration=iteration, 
        selected=SelectionStage.METADATA_APPROVED
        )
    
    if refresh:
        pretty_print("TODO: Refresh the database at iteration", iteration, "for the title check")
  
    choose_elements(articles, db_manager, iteration)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Filter by title')
    parser.add_argument('--iteration', help='iteration number', type=int, required=True)
    parser.add_argument('--db_path', help='db path', type=str, default=search_conf["db_path"])
    parser.add_argument('--refresh', help='refresh the database', action='store_true')
    args = parser.parse_args()
    main(args.iteration, args.db_path, args.refresh)