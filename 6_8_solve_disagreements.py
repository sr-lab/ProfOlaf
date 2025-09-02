import sys
import argparse
from utils.db_management import initialize_db

def solve_disagreements(iteration, search_db_1, search_db_2):
    """
    Solve the disagreements between the two raters.
    """
    db_manager_1 = initialize_db(search_db_1, iteration)
    db_manager_2 = initialize_db(search_db_2, iteration)

    selected_pubs_rater1 = db_manager_1.get_iteration_data(iteration, selected=True)
    selected_pubs_rater2 = db_manager_2.get_iteration_data(iteration, selected=True)
    disagreements = set(selected_pubs_rater1) ^ set(selected_pubs_rater2)
    filtered = set(selected_pubs_rater1) & set(selected_pubs_rater2)
    disagreements = sorted(list(disagreements))

    for i, disagreement in enumerate(disagreements):
        print((f"({i + 1}/{len(disagreements)})"))
        if disagreement in selected_pubs_rater1:
            print("Rater 1 selected the publication: ", disagreement.title)
            print("Rater 2 did not select the publication: ", disagreement.title)
        else:
            print("Rater 1 did not select the publication: ", disagreement.title)
            print("Rater 2 selected the publication: ", disagreement.title)

        print(f"Title: {disagreement.title}")
        print(f"Url: {disagreement.pub_url}")

        while True:
            user_input = input(f"Do you want to keep this element? (y/n/s for skip): ").strip().lower()
            if user_input == 'y':
                db_manager_1.update_iteration_data(iteration, disagreement.id, selected=True)
                db_manager_2.update_iteration_data(iteration, disagreement.id, selected=True)
                break
            elif user_input == 'n':
                db_manager_1.update_iteration_data(iteration, disagreement.id, selected=False)
                db_manager_2.update_iteration_data(iteration, disagreement.id, selected=False)
                break
            else:
                print("Please enter 'y' for yes or 'n' for no.")



if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Filter by title')
    parser.add_argument('--iteration', help='iteration number', type=int)
    parser.add_argument('--search_db_1', help='search db 1', type=str)
    parser.add_argument('--search_db_2', help='search db 2', type=str)
    args = parser.parse_args()
    iteration = args.iteration
    search_db_1 = args.search_db_1
    search_db_2 = args.search_db_2
    solve_disagreements(iteration, search_db_1, search_db_2)