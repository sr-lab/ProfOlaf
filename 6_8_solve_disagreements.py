import sys
import argparse
from enum import Enum
from utils.db_management import DBManager, SelectionStage

class DisagreementStage(Enum):
    TITLE = SelectionStage.TITLE_APPROVED.value
    CONTENT = SelectionStage.CONTENT_APPROVED.value

def solve_disagreements(iteration, search_dbs, selection_stage: DisagreementStage):
    """
    Solve the disagreements between multiple raters.
    """

    db_managers = {search_db: DBManager(search_db) for search_db in search_dbs}
    
    selected_pubs = {search_db: db_manager.get_iteration_data(iteration=iteration, selected=selection_stage) for search_db, db_manager in db_managers.items()}
    
    all_pubs = set()
    for pubs in selected_pubs.values():
        all_pubs.update(pubs)
    disagreements = {}
    for rater, pubs in selected_pubs.items():
        rater_unique_pubs = []
        for pub in pubs:
            selected_by_all = all(pub in selected_pubs[other_rater] for other_rater in search_dbs if other_rater != rater)
            if not selected_by_all:
                rater_unique_pubs.append(pub)
        if rater_unique_pubs:
            disagreements[rater] = rater_unique_pubs
    
    all_disagreements = []
    for pubs in disagreements.values():
        all_disagreements.extend(pubs)
    all_disagreements = list(set(all_disagreements))
    
    for i, disagreement in enumerate(all_disagreements):
        print(f"({i + 1}/{len(all_disagreements)})")
        selected_by = []
        not_selected_by = []
        reasons = {}
        for rater in search_dbs:
            original_rating = db_managers[rater].get_iteration_data(iteration=iteration, id=disagreement.id)[0]
            reasons[rater.replace(".db", "")] = original_rating.title_reason if selection_stage == DisagreementStage.TITLE else original_rating.content_reason
            if original_rating.selected:
                selected_by.append(rater.replace(".db", ""))
            else:
                not_selected_by.append(rater.replace(".db", ""))
        print("\n--------------------------------")
        print(f"Title: {disagreement.title}")
        print(f"Url: {disagreement.pub_url}")
        print(f"Selected by:")
        for rater in selected_by:
            reason = reasons[rater] if reasons[rater] != "" else "No reason provided"
            print(f"{rater}: {reason}")
        print(f"Not selected by:")
        for rater in not_selected_by:
            reason = reasons[rater] if reasons[rater] != "" else "No reason provided"
            print(f"{rater}: {reason}")

        while True:
            user_input = input(f"Do you want to keep this element? (y/n/s for skip): ").strip().lower()
            if user_input == 'y':
                for rater in search_dbs:
                    db_managers[rater].update_iteration_data(iteration, disagreement.id, selected=selection_stage.value)
                break
            elif user_input == 'n':
                for rater in search_dbs:
                    db_managers[rater].update_iteration_data(iteration, disagreement.id, selected=selection_stage.value-1)
                break
            elif user_input == 's':
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