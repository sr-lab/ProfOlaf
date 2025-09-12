import sys
import argparse
from enum import Enum
from utils.db_management import DBManager, SelectionStage

class DisagreementStage(Enum):
    TITLE = SelectionStage.TITLE_APPROVED.value
    ABSTRACT_INTRO = SelectionStage.ABSTRACT_INTRO_APPROVED.value

def solve_disagreements(iteration, search_db_1, search_db_2, selection_stage: DisagreementStage):
    """
    Solve the disagreements between the two raters.
    """

    
    db_manager_1 = DBManager(search_db_1)
    db_manager_2 = DBManager(search_db_2)

    selected_pubs_rater1 = db_manager_1.get_iteration_data(iteration=iteration, selected=selection_stage)
    selected_pubs_rater2 = db_manager_2.get_iteration_data(iteration=iteration, selected=selection_stage)
    disagreements = set(selected_pubs_rater1) ^ set(selected_pubs_rater2)
    filtered = set(selected_pubs_rater1) & set(selected_pubs_rater2)
    
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
                db_manager_1.update_iteration_data(iteration, disagreement.id, selected=selection_stage.value)
                db_manager_2.update_iteration_data(iteration, disagreement.id, selected=selection_stage.value)
                break
            elif user_input == 'n':
                db_manager_1.update_iteration_data(iteration, disagreement.id, selected=selection_stage.value-1)
                db_manager_2.update_iteration_data(iteration, disagreement.id, selected=selection_stage.value-1)
                break
            else:
                print("Please enter 'y' for yes or 'n' for no.")



if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Filter by title')
    parser.add_argument('--iteration', help='iteration number', type=int)
    parser.add_argument('--search_db_1', help='search db 1', type=str)
    parser.add_argument('--search_db_2', help='search db 2', type=str)
    parser.add_argument(
        '--selection_stage', 
        help='selection stage', 
        type=str,
        choices = [e.name for e in DisagreementStage]
    )
    args = parser.parse_args()
    iteration = args.iteration
    search_db_1 = args.search_db_1
    search_db_2 = args.search_db_2
    print(args.selection_stage)
    selection_stage = DisagreementStage[args.selection_stage]
    solve_disagreements(iteration, search_db_1, search_db_2, selection_stage)