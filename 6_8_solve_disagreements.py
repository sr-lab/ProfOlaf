import sys
import argparse
from enum import Enum
from utils.db_management import DBManager, SelectionStage

class DisagreementStage(Enum):
    TITLE = SelectionStage.TITLE_APPROVED.value
    ABSTRACT_INTRO = SelectionStage.ABSTRACT_INTRO_APPROVED.value

def solve_disagreements(iteration, search_dbs, selection_stage: DisagreementStage):
    """
    Solve the disagreements between multiple raters.
    """

    db_managers = {search_db: DBManager(search_db) for search_db in search_dbs}
    
    selected_pubs = {search_db: db_manager.get_iteration_data(iteration=iteration, selected=selection_stage) for search_db, db_manager in db_managers.items()}
    
    # Find all unique publications across all raters
    all_pubs = set()
    for pubs in selected_pubs.values():
        all_pubs.update(pubs)
    
    # Find publications that are not selected by all raters (disagreements)
    disagreements = {}
    for rater, pubs in selected_pubs.items():
        rater_unique_pubs = []
        for pub in pubs:
            # Check if this pub is selected by all other raters
            selected_by_all = all(pub in selected_pubs[other_rater] for other_rater in search_dbs if other_rater != rater)
            if not selected_by_all:
                rater_unique_pubs.append(pub)
        if rater_unique_pubs:
            disagreements[rater] = rater_unique_pubs
    
    # Get all disagreement publications (flatten the dictionary values)
    all_disagreements = []
    for pubs in disagreements.values():
        all_disagreements.extend(pubs)
    all_disagreements = list(set(all_disagreements))  # Remove duplicates
    
    for i, disagreement in enumerate(all_disagreements):
        print(f"({i + 1}/{len(all_disagreements)})")
        
        # Show which raters selected/didn't select this publication
        selected_by = []
        not_selected_by = []
        for rater in search_dbs:
            if disagreement in selected_pubs[rater]:
                selected_by.append(rater.replace(".db", ""))
            else:
                not_selected_by.append(rater.replace(".db", ""))
        print("\n--------------------------------")
        print(f"Title: {disagreement.title}")
        print(f"Url: {disagreement.pub_url}")
        print(f"Selected by: {', '.join(selected_by)}")
        print(f"Not selected by: {', '.join(not_selected_by)}")

        while True:
            user_input = input(f"Do you want to keep this element? (y/n/s for skip): ").strip().lower()
            if user_input == 'y':
                # Update all raters to select this publication
                for rater in search_dbs:
                    db_managers[rater].update_iteration_data(iteration, disagreement.id, selected=selection_stage.value)
                break
            elif user_input == 'n':
                # Update all raters to not select this publication
                for rater in search_dbs:
                    db_managers[rater].update_iteration_data(iteration, disagreement.id, selected=selection_stage.value-1)
                break
            elif user_input == 's':
                # Skip this disagreement
                break
            else:
                print("Please enter 'y' for yes, 'n' for no, or 's' for skip.")



if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Filter by title')
    parser.add_argument('--iteration', help='iteration number', type=int)
    # allow multiple search dbs
    parser.add_argument('--search_dbs', help='search dbs', type=str, nargs='+')
    parser.add_argument(
        '--selection_stage', 
        help='selection stage', 
        type=str,
        choices = [e.name for e in DisagreementStage]
    )
    args = parser.parse_args()
    iteration = args.iteration
    search_dbs = args.search_dbs
    selection_stage = DisagreementStage[args.selection_stage]
    solve_disagreements(iteration, search_dbs, selection_stage)