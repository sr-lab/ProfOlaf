import sqlite3
import json
import os
import time
from pathlib import Path
from typing import Dict, Any, Optional, List
from openai import OpenAI
from tqdm import tqdm
from pydantic import BaseModel, Field, create_model

from ..db_management import DBManager, SelectionStage

from ..article_processing.shared_utils import (
    PDFProcessor,
    load_config,
    create_llm,
    get_use_chat_model,
    truncate_text,
)

from ..article_processing.download_pdfs import download_pdf, is_valid_pdf

PROMPTS_FOLDER = "prompts"
SYSTEM_CONTENT_PROMPT_FILE = os.path.join(PROMPTS_FOLDER, "system_content_screening.txt")
USER_CONTENT_PROMPT_FILE = os.path.join(PROMPTS_FOLDER, "user_content_screening.txt")
SYSTEM_TITLE_PROMPT_FILE = os.path.join(PROMPTS_FOLDER, "system_title_screening.txt")
USER_TITLE_PROMPT_FILE = os.path.join(PROMPTS_FOLDER, "user_title_screening.txt")

class BaseScreeningResult(BaseModel):
    title: str = Field(description="The title of the paper")
    keep: bool = Field(description="Whether to keep the paper")
    reason: str = Field(description="The reason for the decision")

def get_articles_from_db(db_path: str, iteration: int, stage="title"):
    db_manager = DBManager(db_path)

    if stage == "title":
        # Check if metadata filtering step was skipped
        workflow_state_path = "confs/workflow_state.json"
        metadata_skipped = False
        if os.path.exists(workflow_state_path):
            try:
                import json
                with open(workflow_state_path, 'r', encoding='utf-8') as f:
                    workflow_state = json.load(f)
                    skipped_steps = workflow_state.get('skipped_steps', [])
                    metadata_skipped = 'Step 5: Filter by Metadata' in skipped_steps or 'Step 4: Filter by Metadata' in skipped_steps
            except Exception:
                pass
        
        if metadata_skipped:
            # Get articles that are NOT_SELECTED or METADATA_APPROVED but not yet title-filtered
            articles_metadata = db_manager.get_iteration_data(
                iteration=iteration,
                selected=SelectionStage.METADATA_APPROVED
            )
            articles_not_selected = db_manager.get_iteration_data(
                iteration=iteration,
                selected=SelectionStage.NOT_SELECTED
            )
            # Combine and deduplicate by ID
            article_dict = {}
            for article in articles_metadata:
                article_dict[article.id] = article
            for article in articles_not_selected:
                if article.id not in article_dict:
                    article_dict[article.id] = article
            articles = list(article_dict.values())
        else:
            # Normal case: get METADATA_APPROVED articles
            # But also check for NOT_SELECTED if no METADATA_APPROVED found (in case step wasn't run)
            articles = db_manager.get_iteration_data(
                iteration=iteration,
                selected=SelectionStage.METADATA_APPROVED
            )
            # If no METADATA_APPROVED articles found, also try NOT_SELECTED
            # (this handles the case where metadata filtering step wasn't run)
            if len(articles) == 0:
                articles = db_manager.get_iteration_data(
                    iteration=iteration,
                    selected=SelectionStage.NOT_SELECTED
                )
        
        # Filter out articles that are already title-approved or higher
        filtered_articles = []
        for article in articles:
            selected_int = int(article.selected) if article.selected is not None else 0
            # Only include if not already title-approved or higher
            if selected_int < SelectionStage.TITLE_APPROVED.value:
                filtered_articles.append(article)
        
        return filtered_articles
    elif stage == "content":
        selected = SelectionStage.TITLE_APPROVED
        articles = db_manager.get_iteration_data(
            iteration=iteration,
            selected=selected
        )
        return articles
    else:
        print("Invalid stage")
        return []

def update_screening_result_class(annotations: dict[str, str]):
    return create_model(
        "DynamicScreeningResult",
        __base__=BaseScreeningResult,
        **{annotation: (str, Field(description=annotations[annotation])) for annotation in annotations},
    )

def ask_model(
    system_prompt: str,
    user_prompt: str,
    model: str = "gpt-4o",
    api_key: Optional[str] = None,
    temperature: float = 0.7,
    annotations: dict[str, str] = {},
):

    if len(annotations.keys()) > 0:
        ScreeningResult = update_screening_result_class(annotations)
    else:
        ScreeningResult = BaseScreeningResult
    client = OpenAI(api_key=api_key or os.getenv("OPENAI_API_KEY"))
    try:
        response = client.responses.parse(
            model=model,
            input=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt,},
            ],
            text_format=ScreeningResult,
        )
    except json.JSONDecodeError as e:
        raise ValueError(f"Failed to parse JSON response: {e}")
    except Exception as e:
        raise RuntimeError(f"Error calling OpenAI API: {e}")

    return dict(response.output_parsed)

def process_api_key(api_key: Optional[str]):
    if api_key is None:
        return os.getenv("OPENAI_API_KEY")
    elif api_key.endswith(".txt"):
        return open(api_key, "r").read().strip()
    return api_key

def screen_papers(
    rater_id: str,
    topic: str,
    db_path: str,
    iteration: int,
    stage: str,
    model: str = "gpt-4o",
    api_key: Optional[str] = None,
    temperature: float = 0.7,
    annotations: dict[str, str] = {},
    article_folder: Optional[str] = None,
    system_prompt_file: Optional[str] = None,
    user_prompt_file: Optional[str] = None,
):
    articles = get_articles_from_db(db_path, iteration, stage)
    api_key = process_api_key(api_key)
    results = []
    for article in articles:
        content = ""
        keep_key = "keep_title"
        reason_key = "title_reason"
        if stage == "content":
            content = PDFProcessor.extract_text_from_pdf(os.path.join(article_folder, f"{article.id}.pdf"))
            keep_key = "keep_content"
            reason_key = "content_reason"
        if system_prompt_file is None:
            system_prompt_file = SYSTEM_CONTENT_PROMPT_FILE if stage == "content" else SYSTEM_TITLE_PROMPT_FILE
        if user_prompt_file is None:
            user_prompt_file = USER_CONTENT_PROMPT_FILE if stage == "content" else USER_TITLE_PROMPT_FILE
        system_prompt = open(system_prompt_file, "r").read()
        user_prompt = open(user_prompt_file, "r").read().format(title=article.title, content=content, topic=topic)
        result = ask_model(system_prompt, user_prompt, model, api_key, temperature, annotations=annotations)
        result["id"] = article.id
        result["rater"] = rater_id
        result["iteration"] = article.iteration
        result["title"] = article.title
        result[keep_key] = result.pop("keep")
        result[reason_key] = result.pop("reason")
        results.append(result)
    
    db_manager = DBManager(db_path)
    for result in results:
        db_manager.insert_screening_data(
            article_id=result["id"],
            rater=rater_id,
            iteration=result["iteration"],
            keep=result[keep_key],
            reason=result[reason_key],
            screening_phase=stage,
            title=result["title"],
            **{
                annotation: result[annotation] 
                for annotation in annotations.keys()
            }
        )
    return results

def download_manually(articles, article_folder: str):
    for article in articles:
        response = input("Have you completed the download? (y/n): ").lower().strip()
        if response in ['y', 'yes']:
            file_path = os.path.join(article_folder, f"{article.id}.pdf")
            if os.path.exists(file_path) and is_valid_pdf(file_path):
                print(f"✓ File {article.id}.pdf verified successfully!")
                break
            else:
                if os.path.exists(file_path):
                    print(f"✗ File {article.id}.pdf exists but is not a valid PDF. \
                        Please ensure it's a valid PDF file.")
                else:
                    print(f"✗ File {article.id}.pdf not found. \
                        Please ensure it's saved correctly.")
                continue
        elif response in ['n', 'no']:
            print("Please complete the download and try again.")
            continue
        else:
            print("Please answer with 'y' or 'n'.")

    print(f"\nDownload complete. Files saved to: {article_folder}/")

def download_pdfs(articles, article_folder: str, skip_manual_prompt: bool = False):
    """
    Download PDFs for articles.
    
    Args:
        articles: List of articles to download PDFs for
        article_folder: Folder to save PDFs to
        skip_manual_prompt: If True, return failed downloads instead of prompting user
    
    Returns:
        List of failed article downloads (if skip_manual_prompt=True), otherwise None
    """
    failed_downloads = []
    for article in tqdm(articles, desc="Downloading PDFs"):
        url = article.eprint_url or article.pub_url
        if not url:
            failed_downloads.append(article)
            continue
        output_file = os.path.join(article_folder, f"{article.id}.pdf")
        if os.path.exists(output_file):
            # Verify existing file is valid
            if is_valid_pdf(output_file):
                continue
            else:
                # Remove invalid PDF and retry
                os.remove(output_file)
        if download_pdf(url, output_file):
            if is_valid_pdf(output_file):
                print(f"Successfully downloaded and verified: {output_file}")
                time.sleep(1)
            else:
                print(f"Downloaded but invalid PDF: {output_file}")
                os.remove(output_file)
                failed_downloads.append(article)
        else:
            failed_downloads.append(article)
    
    if failed_downloads:
        if skip_manual_prompt:
            return failed_downloads
        else:
            print("\nFailed downloads:")
            download_manually(failed_downloads, article_folder)
    
    return failed_downloads if skip_manual_prompt else None
