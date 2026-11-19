#!/usr/bin/env python3
"""
ProfOlaf Web Application - Entry Point
"""

import os
import json
import glob
import threading
import shutil
import sqlite3
from flask import Flask, render_template, request, jsonify, flash, redirect, url_for, send_file
from pathlib import Path
from collections import defaultdict
from utils.db_management import DBManager, SelectionStage, initialize_db
from utils.article_search.article_search_method import (
    SearchMethod, ArticleSearch, SemanticScholarSearchMethod, GoogleScholarSearchMethod, DBLPSearchMethod,
    _get_semantic_scholar_api_key,
)
from werkzeug.utils import secure_filename

# Import from pipeline modules
from utils.pipeline.generate_snowball_start_utils import generate_snowball_start, extract_titles_from_file
from utils.pipeline.start_iteration_utils import get_articles, snowball_direction_kwargs, SNOWBALL_DIRECTIONS
from utils.pipeline.get_bibtex import process_articles_optimized, get_bibtex_single
from utils.pipeline.generate_conf_rank_utils import get_venues, find_similar_venues, _get_scimago_rank, _get_core_rank
from utils.pipeline.filter_by_metadata_utils import automated_check_venue_and_peer_reviewed, is_preprint_publication
from utils.pipeline.screening import apply_decision
from utils.pipeline.solve_disagreements import settle_agreements
from utils.pipeline.llm_screening import screen_papers, download_pdfs, get_articles_from_db
from utils.pipeline.citation_graph_utils import generate_citation_graph, EDGE_SOURCES, DEFAULT_FORMATS
from utils.pipeline.cluster_graph_utils import generate_cluster_graphs, get_paper_links, MANIFEST_NAME
from utils.article_processing.download_pdfs import is_valid_pdf

# Global state for tracking running tasks
running_tasks = {
    'generate_snowball_start': {
        'is_running': False,
        'progress': 0,
        'total': 0,
        'current_step': '',
        'logs': [],
        'cancel_flag': None
    },
    'start_iteration': {
        'is_running': False,
        'progress': 0,
        'total': 0,
        'current_step': '',
        'logs': [],
        'cancel_flag': None,
        'articles_without_id_count': 0,
        'articles_without_id_iteration': None
    },
    'get_bibtex': {
        'is_running': False,
        'progress': 0,
        'total': 0,
        'current_step': '',
        'logs': [],
        'cancel_flag': None
    },
    'generate_citation_graph': {
        'is_running': False,
        'progress': 0,
        'total': 0,
        'current_step': '',
        'logs': [],
        'cancel_flag': None,
        'outputs': {}
    },
    'generate_cluster_graphs': {
        'is_running': False,
        'progress': 0,
        'total': 0,
        'current_step': '',
        'logs': [],
        'cancel_flag': None,
        'outputs': {}
    }
}

ITERATION_0 = 0

# Configuration for file uploads
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'txt', 'json'}
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app = Flask(__name__)
app.secret_key = os.urandom(24)

# Configuration file path
CONFS_DIR = "confs"
DATABASES_DIR = "databases"
SEARCH_CONF_PATH = os.path.join(CONFS_DIR, "search_conf.json")
WORKFLOW_STATE_PATH = os.path.join(CONFS_DIR, "workflow_state.json")
ANALYSIS_CONF_PATH = os.path.join(CONFS_DIR, "analysis_conf.json")
LLM_CONFIG_PATH = os.path.join("utils", "article_llm_analysis", "llm_config.json")
GRAPHS_DIR = os.path.join("static", "graphs")
# Per-cluster graphs are shared with the CLI (13_generate_cluster_graphs.py).
CLUSTERS_DIR = os.path.join("graphs", "clusters")

# Ensure directories exist
os.makedirs(CONFS_DIR, exist_ok=True)
os.makedirs(DATABASES_DIR, exist_ok=True)


def load_workflow_state():
    """Load workflow state from JSON file"""
    try:
        if os.path.exists(WORKFLOW_STATE_PATH):
            with open(WORKFLOW_STATE_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        else:
            return {
                'db_path': None,
                'current_iteration': None,
                'last_step': None,
                'skipped_steps': []
            }
    except Exception as e:
        print(f"Error loading workflow state: {e}")
        return {
            'db_path': None,
            'current_iteration': None,
            'last_step': None,
            'skipped_steps': []
        }


def save_workflow_state(state):
    """Save workflow state to JSON file"""
    try:
        # Ensure confs directory exists
        os.makedirs(CONFS_DIR, exist_ok=True)
        with open(WORKFLOW_STATE_PATH, 'w', encoding='utf-8') as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        print(f"Error saving workflow state: {e}")
        return False


def get_db_manager_for_workflow():
    """Helper function to get DBManager for workflow metadata operations"""
    search_conf = load_search_conf()
    if search_conf and 'db_path' in search_conf:
        db_path = search_conf['db_path']
    else:
        db_path = os.path.join(DATABASES_DIR, 'database.db')
    
    if not os.path.exists(db_path):
        return None
    
    try:
        db_manager = DBManager(db_path)
        # Ensure workflow metadata table exists (for existing databases)
        try:
            db_manager.create_workflow_metadata_table()
        except:
            pass  # Table might already exist
        return db_manager
    except Exception as e:
        print(f"Error creating DBManager for workflow: {e}")
        return None


def update_workflow_state(db_path=None, current_iteration=None, last_step=None, skip_step=None):
    """Update workflow state in both database and JSON file (for backwards compatibility)"""
    state = load_workflow_state()
    
    # Ensure skipped_steps list exists
    if 'skipped_steps' not in state:
        state['skipped_steps'] = []
    
    if db_path is not None:
        state['db_path'] = db_path
    if current_iteration is not None:
        state['current_iteration'] = current_iteration
    if last_step is not None:
        state['last_step'] = last_step
    if skip_step is not None:
        # Add step to skipped list if not already there
        if skip_step not in state['skipped_steps']:
            state['skipped_steps'].append(skip_step)
    
    # Update database metadata (primary source)
    db_manager = get_db_manager_for_workflow()
    if db_manager:
        try:
            if current_iteration is not None:
                db_manager.update_current_iteration(current_iteration)
            if last_step is not None:
                db_manager.update_last_step(last_step)
            db_manager.conn.close()
        except Exception as e:
            print(f"Error updating workflow metadata in database: {e}")
    
    # Also save to JSON for backwards compatibility
    save_workflow_state(state)
    return state


def get_next_step_after_skip(current_step):
    """Determine the next logical step after skipping a step"""
    step_map = {
        "Step 0: Generate Snowball Start": "Step 1: Start Iteration",
        "Step 1: Start Iteration": "Step 2: Remove Duplicates",
        "Step 2: Remove Duplicates": "Step 3: Get BibTeX",
        "Step 3: Get BibTeX": "Step 4: Assign Venue Ranks",
        "Step 4: Assign Venue Ranks": "Step 5: Filter by Metadata",
        "Step 5: Filter by Metadata": "Step 6: Filter by Title",
        "Step 6: Filter by Title": "Step 7: Solve Title Disagreements",
        "Step 7: Solve Title Disagreements": "Step 8: Filter by Content",
        "Step 8: Filter by Content": "Step 9: Solve Content Disagreements",
        "Step 9: Solve Content Disagreements": "Step 10: Generate CSV"
    }
    return step_map.get(current_step, None)


def generate_search_conf(data):
    """
    Generate search configuration dictionary from form data.
    
    Args:
        data: Dictionary containing form data
        
    Returns:
        Dictionary containing the search configuration
    """
    # Parse venue ranks from comma-separated string
    venue_list_str = data.get('venue_rank_list', '')
    venue_list = [v.strip() for v in venue_list_str.split(',') if v.strip()]
    
    # Parse annotations from newline-separated string
    annotations_str = data.get('annotations', '')
    annotations = [a.strip() for a in annotations_str.split('\n') if a.strip()]
    
    # Handle proxy key - check if it's a file path or direct value
    proxy_key = data.get('proxy_key', '').strip()
    if proxy_key and data.get('proxy_from_file') == 'true':
        # Read proxy key from file
        try:
            with open(proxy_key, 'r', encoding='utf-8') as f:
                proxy_key = f.read().strip()
        except Exception as e:
            raise ValueError(f"Failed to read proxy key file: {str(e)}")
    
    # Use the provided database path as-is; only default to databases/ when none given
    db_path = data.get('db_path', '').strip()
    if not db_path:
        db_path = os.path.join(DATABASES_DIR, 'database.db')
    else:
        # Resolve relative or bare filenames relative to cwd (do not force into databases/)
        db_path = os.path.abspath(db_path)
    
    config = {
        "start_year": int(data.get('start_year', 2020)),
        "end_year": int(data.get('end_year', 2024)),
        "venue_rank_list": venue_list,
        "proxy_key": proxy_key,
        "initial_file": data.get('initial_file', 'confs/seed.txt'),
        "db_path": db_path,
        "csv_path": data.get('csv_path', 'results.csv'),
        "search_method": data.get('search_method', 'google_scholar'),
        "annotations": annotations,
        "rater": data.get('rater', 'default')
    }
    
    return config


def load_search_conf():
    """Load search configuration if it exists"""
    if os.path.exists(SEARCH_CONF_PATH):
        try:
            with open(SEARCH_CONF_PATH, 'r') as f:
                return json.load(f)
        except Exception:
            return None
    return None


def get_current_iteration_from_db(db_manager, all_articles):
    """
    Determine the current iteration from the database.
    First checks if there's a stored current_iteration in search_conf.
    Otherwise falls back to MAX(iteration) from articles.
    """
    # Try to get from search_conf first
    search_conf = load_search_conf()
    if search_conf and 'current_iteration' in search_conf:
        stored_iteration = search_conf['current_iteration']
        if stored_iteration is not None:
            try:
                return int(stored_iteration)
            except (ValueError, TypeError):
                pass
    
    # Fall back to database method
    try:
        result = db_manager.check_current_iteration()
        if result:
            iteration = result[0]
            if iteration is not None:
                try:
                    return int(iteration)
                except (ValueError, TypeError):
                    pass
    except:
        pass
    
    # Last resort: calculate from articles
    if all_articles:
        iteration_max = 0
        for article in all_articles:
            try:
                iter_num = int(article.iteration) if article.iteration else 0
                iteration_max = max(iteration_max, iter_num)
            except (ValueError, TypeError):
                continue
        return iteration_max if iteration_max >= 0 else None
    
    return None


def update_current_iteration(iteration):
    """
    Update the current_iteration in search_conf.json
    """
    search_conf = load_search_conf()
    if search_conf is None:
        search_conf = {}
    
    try:
        iteration_int = int(iteration)
        search_conf['current_iteration'] = iteration_int
        
        # Ensure confs directory exists
        os.makedirs(CONFS_DIR, exist_ok=True)
        with open(SEARCH_CONF_PATH, 'w') as f:
            json.dump(search_conf, f, indent=4)
        return True
    except (ValueError, TypeError, Exception):
        return False


def get_workflow_info():
    """
    Get workflow information (current iteration, step, counts, etc.)
    Returns a dictionary with workflow_info or None if database doesn't exist
    """
    search_conf = load_search_conf()
    
    # Get database path
    if search_conf and 'db_path' in search_conf:
        db_path = search_conf['db_path']
    else:
        db_path = os.path.join(DATABASES_DIR, 'database.db')
    
    db_exists = os.path.exists(db_path)
    
    # Load workflow state (used as fallback/primary source)
    workflow_state = load_workflow_state()
    
    if not db_exists:
        # If no database exists and no workflow state, show default message
        last_step = workflow_state.get('last_step')
        if not last_step:
            last_step = None  # Will be displayed as "No steps performed yet" in templates
        return {
            'current_iteration': workflow_state.get('current_iteration'),
            'current_step': last_step,
            'content_approved_count': 0,
            'new_articles_count': 0,
            'total_articles': 0,
            'search_method': None
        }
    
    try:
        db_manager = DBManager(db_path)
        
        # Ensure workflow metadata table exists
        try:
            db_manager.create_workflow_metadata_table()
        except:
            pass  # Table might already exist
        
        # Try to get current iteration and last step from database metadata (primary source)
        current_iteration = db_manager.get_current_iteration()
        current_step = db_manager.get_last_step()
        
        # If database doesn't have metadata yet, try to migrate from workflow_state.json
        if current_iteration is None and workflow_state.get('current_iteration') is not None:
            current_iteration = workflow_state.get('current_iteration')
            db_manager.update_current_iteration(current_iteration)
        
        if current_step is None and workflow_state.get('last_step') is not None:
            current_step = workflow_state.get('last_step')
            db_manager.update_last_step(current_step)
        
        # If still no current iteration, try to infer from database
        if current_iteration is None:
            all_articles = db_manager.get_iteration_data()
            current_iteration = get_current_iteration_from_db(db_manager, all_articles)
            if current_iteration is not None:
                db_manager.update_current_iteration(current_iteration)
        
        # Get all articles to calculate stats
        all_articles = db_manager.get_iteration_data()
        
        # Get max_selected and search_method for the current iteration
        max_selected = None
        search_method = None
        if current_iteration is not None:
            try:
                result = db_manager.check_current_iteration()
                if result:
                    iter_from_db, max_selected, search_method = result
            except:
                pass
        
        # For iteration 0, use "Step 0" only if we don't have a stored step that's later
        # (iteration 0 articles are auto-approved but didn't go through the steps)
        # IMPORTANT: Only override if we're actually on iteration 0 AND we don't have a stored step
        # If we have a stored step that's later (e.g., "Step 1: Start Iteration"), respect it
        if current_iteration == 0:
            # Only override to Step 0 if we don't have a stored step, or if the stored step is Step 0
            # This ensures that if step 1 has been executed (which updates current_iteration to >= 1),
            # we won't be in this branch, and the stored step will be used
            if current_step is None or current_step == "Step 0: Generate Snowball Start":
                current_step = "Step 0: Generate Snowball Start"
                # Save this to database to ensure it's persisted
                try:
                    db_manager.update_last_step(current_step)
                except:
                    pass
            # If we have a stored step that's later than Step 0 (e.g., Step 1), keep it
            # This handles edge cases where step 1 might have been executed but current_iteration
            # hasn't been updated yet (shouldn't happen, but defensive programming)
        # If still no current step, set default
        elif current_step is None:
            current_step = "Step 0: Generate Snowball Start"
        
        # Only infer step if we don't have an explicit last_step from database
        # (and we're not on iteration 0, which is handled above)
        if current_step is None and current_iteration != 0:
                if max_selected is not None:
                    try:
                        max_selected_int = int(max_selected)
                        if max_selected_int == 0:
                            current_step = "Step 1-2: Initial Setup & BibTeX"
                        elif max_selected_int == 1:
                            current_step = "Step 5: Filter by Metadata"
                        elif max_selected_int == 2:
                            current_step = "Step 6: Title Screening"
                        elif max_selected_int == 3:
                            # Only infer Step 7 if we're not on iteration 0
                            # (iteration 0 articles are auto-approved, but iteration > 0 articles need to go through the step)
                            current_step = "Step 8: Content Screening"
                        else:
                            current_step = f"Step: Selection Stage {max_selected_int}"
                    except (ValueError, TypeError):
                        current_step = "Step: Unknown"
                    
                    # Save inferred step to database
                    try:
                        db_manager.update_last_step(current_step)
                    except:
                        pass
                else:
                    if all_articles:
                        has_bibtex = any(getattr(a, 'bibtex', '') for a in all_articles if hasattr(a, 'bibtex'))
                        if has_bibtex:
                            current_step = "Step 4: Assign Venue Ranks"
                        else:
                            current_step = "Step 3: Get BibTeX"
                    else:
                        current_step = "Step 0: Generate Snowball Start"
                    
                    # Save inferred step to database
                    try:
                        db_manager.update_last_step(current_step)
                    except:
                        pass
        elif current_step == "Step 0: Generate Snowball Start" and current_iteration == 0:
            # If we have Step 0 and we're on iteration 0, make sure it's saved (might have been inferred before)
            try:
                db_manager.update_last_step(current_step)
            except:
                pass
        
        # Count content approved papers (selected = 3)
        content_approved_count = 0
        for article in all_articles:
            try:
                selected = int(article.selected) if article.selected is not None else 0
                if selected == 3:  # CONTENT_APPROVED
                    content_approved_count += 1
            except (ValueError, TypeError):
                continue
        
        # Count articles in current iteration
        new_articles_count = 0
        if current_iteration is not None:
            try:
                current_iter_int = int(current_iteration)
                for article in all_articles:
                    try:
                        article_iter = getattr(article, 'iteration', None)
                        if article_iter is not None:
                            article_iter_int = int(article_iter)
                            if article_iter_int == current_iter_int:
                                new_articles_count += 1
                    except (ValueError, TypeError, AttributeError):
                        continue
            except (ValueError, TypeError):
                new_articles_count = 0
        
        workflow_info = {
            'current_iteration': current_iteration,
            'current_step': current_step,
            'content_approved_count': content_approved_count,
            'new_articles_count': new_articles_count,
            'total_articles': len(all_articles),
            'search_method': search_method
        }
        
        # Close database connection
        db_manager.conn.close()
        
        # Sync workflow state JSON with database for backwards compatibility
        if workflow_state.get('db_path') != db_path or workflow_state.get('current_iteration') != current_iteration or workflow_state.get('last_step') != current_step:
            update_workflow_state(
                db_path=db_path,
                current_iteration=current_iteration,
                last_step=current_step
            )
        
        return workflow_info
        
    except Exception as e:
        return {
            'current_iteration': None,
            'current_step': "Error",
            'content_approved_count': 0,
            'new_articles_count': 0,
            'total_articles': 0,
            'search_method': None,
            'error': str(e)
        }


@app.route('/')
def index():
    """Main dashboard page"""
    # Check if search_conf.json exists
    config_exists = os.path.exists(SEARCH_CONF_PATH)
    
    # Check if database exists (from config if available)
    db_exists = False
    db_path = None
    search_conf = load_search_conf()
    if search_conf and 'db_path' in search_conf:
        db_path = search_conf['db_path']
        db_exists = os.path.exists(db_path)
    
    return render_template('index.html', 
                         config_exists=config_exists,
                         db_exists=db_exists,
                         db_path=db_path)


@app.route('/generate_search_conf', methods=['GET', 'POST'])
def generate_search_conf_route():
    """Generate search configuration"""
    if request.method == 'GET':
        # Load existing configuration if it exists
        existing_config = load_search_conf()
        
        # Load existing seed.txt if it exists
        seed_content = ""
        seed_file_path = os.path.join(CONFS_DIR, "seed.txt")
        if os.path.exists(seed_file_path):
            try:
                with open(seed_file_path, 'r', encoding='utf-8') as f:
                    seed_content = f.read()
            except Exception:
                pass
        
        # Prepare default values from existing config or use defaults
        initial_file = existing_config.get('initial_file', 'confs/seed.txt') if existing_config else 'confs/seed.txt'
        # Normalize old format to new format
        if initial_file == 'seed.txt':
            initial_file = 'confs/seed.txt'
        
        defaults = {
            'start_year': existing_config.get('start_year', 2020) if existing_config else 2020,
            'end_year': existing_config.get('end_year', 2024) if existing_config else 2024,
            'venue_rank_list': ', '.join(existing_config.get('venue_rank_list', ['A*', 'A', 'B', 'C', 'Q1', 'Q2'])) if existing_config else 'A*, A, B, C, Q1, Q2',
            'search_method': existing_config.get('search_method', 'google_scholar') if existing_config else 'google_scholar',
            'proxy_key': existing_config.get('proxy_key', '') if existing_config else '',
            'initial_file': initial_file,
            'db_path': existing_config.get('db_path', os.path.join(DATABASES_DIR, 'database.db')) if existing_config else os.path.join(DATABASES_DIR, 'database.db'),
            'csv_path': existing_config.get('csv_path', 'results.csv') if existing_config else 'results.csv',
            'rater': existing_config.get('rater', 'default') if existing_config else 'default',
            'annotations': '\n'.join(existing_config.get('annotations', [])) if existing_config else '',
            'seed_content': seed_content
        }
        
        # Show the form
        return render_template('generate_search_conf.html', **defaults)
    
    # Handle POST request
    try:
        # Get form data
        form_data = request.form.to_dict()
        
        # Validate required fields
        if not form_data.get('start_year') or not form_data.get('end_year'):
            flash('Start year and end year are required', 'error')
            return redirect(url_for('generate_search_conf_route'))
        
        if not form_data.get('initial_file'):
            flash('Initial file is required', 'error')
            return redirect(url_for('generate_search_conf_route'))
        
        if not form_data.get('db_path'):
            flash('Database path is required', 'error')
            return redirect(url_for('generate_search_conf_route'))
        
        if not form_data.get('csv_path'):
            flash('CSV path is required', 'error')
            return redirect(url_for('generate_search_conf_route'))
        
        # Validate year range
        start_year = int(form_data.get('start_year'))
        end_year = int(form_data.get('end_year'))
        
        if start_year >= end_year:
            flash('Starting year must be less than ending year', 'error')
            return redirect(url_for('generate_search_conf_route'))
        
        # Generate configuration
        config = generate_search_conf(form_data)
        
        # Ensure confs directory exists
        os.makedirs(CONFS_DIR, exist_ok=True)
        
        # Save seed.txt content if provided
        seed_content = form_data.get('seed_content', '').strip()
        if seed_content:
            seed_file_path = os.path.join(CONFS_DIR, "seed.txt")
            with open(seed_file_path, 'w', encoding='utf-8') as f:
                f.write(seed_content)
        
        # Update initial_file to use confs/seed.txt if it's the default
        initial_file = config.get('initial_file', 'confs/seed.txt')
        if initial_file == 'seed.txt' or initial_file == 'confs/seed.txt':
            config['initial_file'] = 'confs/seed.txt'
        
        # Save configuration
        with open(SEARCH_CONF_PATH, 'w') as f:
            json.dump(config, f, indent=4)
        
        flash('Search configuration generated successfully!', 'success')
        return redirect(url_for('configuration'))
        
    except ValueError as e:
        flash(f'Validation error: {str(e)}', 'error')
        return redirect(url_for('generate_search_conf_route'))
    except Exception as e:
        flash(f'Error generating configuration: {str(e)}', 'error')
        return redirect(url_for('generate_search_conf_route'))


@app.route('/configuration', methods=['GET'])
def configuration():
    """Configuration page - view and generate search configuration"""
    config_exists = os.path.exists(SEARCH_CONF_PATH)
    current_config = None
    
    if config_exists:
        try:
            with open(SEARCH_CONF_PATH, 'r') as f:
                current_config = json.load(f)
        except Exception:
            current_config = None
    
    return render_template('configuration.html', 
                         config_exists=config_exists, 
                         current_config=current_config)


ALLOWED_DB_EXTENSIONS = {'db', 'sqlite', 'sqlite3'}


def _validate_and_set_db_path(db_path: str):
    """Validate db_path and update search_conf. Returns (success, response_tuple)."""
    if not db_path or not db_path.strip():
        return False, (jsonify({'success': False, 'error': 'Database path cannot be empty'}), 400)
    db_path = db_path.strip()
    if not os.path.exists(db_path):
        return False, (jsonify({
            'success': False,
            'error': f'Database file not found: {db_path}. Please check the path and try again.'
        }), 404)
    try:
        db_manager = DBManager(db_path)
        _ = db_manager.get_iteration_data()
    except Exception as e:
        return False, (jsonify({
            'success': False,
            'error': f'Invalid database file: {str(e)}'
        }), 400)
    search_conf = load_search_conf()
    if search_conf is None:
        search_conf = {
            'start_year': 2020,
            'end_year': 2024,
            'venue_rank_list': ['A*', 'A', 'B', 'C', 'Q1', 'Q2'],
            'proxy_key': '',
            'initial_file': 'confs/seed.txt',
            'db_path': db_path,
            'csv_path': 'results.csv',
            'search_method': 'google_scholar',
            'annotations': ['Methods', 'Area'],
            'current_iteration': None
        }
    else:
        search_conf['db_path'] = db_path
    os.makedirs(CONFS_DIR, exist_ok=True)
    with open(SEARCH_CONF_PATH, 'w') as f:
        json.dump(search_conf, f, indent=4)
    return True, (jsonify({
        'success': True,
        'message': f'Database loaded successfully: {db_path}',
        'db_path': db_path
    }), 200)


@app.route('/api/database/upload', methods=['POST'])
def upload_database():
    """Upload a database file to the project's databases folder and set it as the current database."""
    try:
        if 'database_file' not in request.files:
            return jsonify({'success': False, 'error': 'No file provided'}), 400
        file = request.files['database_file']
        if not file or file.filename == '':
            return jsonify({'success': False, 'error': 'No file selected'}), 400
        ext = (file.filename.rsplit('.', 1)[-1] or '').lower()
        if ext not in ALLOWED_DB_EXTENSIONS:
            return jsonify({
                'success': False,
                'error': f'Invalid file type. Allowed: {", ".join(ALLOWED_DB_EXTENSIONS)}'
            }), 400
        filename = secure_filename(file.filename) or 'uploaded.db'
        if not filename.lower().endswith(('.db', '.sqlite', '.sqlite3')):
            filename += '.db'
        os.makedirs(DATABASES_DIR, exist_ok=True)
        db_path = os.path.abspath(os.path.join(DATABASES_DIR, filename))
        file.save(db_path)
        ok, result = _validate_and_set_db_path(db_path)
        return result
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/database/upload_file', methods=['POST'])
def upload_database_file():
    """Upload a database file to the project's databases folder. Returns the path for use in merge steps (does not set as current database)."""
    try:
        if 'database_file' not in request.files:
            return jsonify({'success': False, 'error': 'No file provided'}), 400
        file = request.files['database_file']
        if not file or file.filename == '':
            return jsonify({'success': False, 'error': 'No file selected'}), 400
        ext = (file.filename.rsplit('.', 1)[-1] or '').lower()
        if ext not in ALLOWED_DB_EXTENSIONS:
            return jsonify({
                'success': False,
                'error': f'Invalid file type. Allowed: {", ".join(ALLOWED_DB_EXTENSIONS)}'
            }), 400
        filename = secure_filename(file.filename) or 'uploaded.db'
        if not filename.lower().endswith(('.db', '.sqlite', '.sqlite3')):
            filename += '.db'
        os.makedirs(DATABASES_DIR, exist_ok=True)
        dest_path = os.path.join(DATABASES_DIR, filename)
        file.save(dest_path)
        # Return path relative to project root so merge APIs can resolve it
        db_path = os.path.join(DATABASES_DIR, filename)
        return jsonify({'success': True, 'db_path': db_path})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/database/load', methods=['POST'])
def load_database():
    """Load a new database file and update search_conf.json (by path or from JSON body)."""
    try:
        # Support both: JSON with db_path, or form data with path (for consistency)
        if request.is_json:
            data = request.get_json() or {}
            db_path = (data.get('db_path') or '').strip()
        else:
            db_path = (request.form.get('db_path') or '').strip()
        if not db_path:
            return jsonify({'success': False, 'error': 'Database path not provided'}), 400
        # Use the path as-is: resolve relative/bare paths relative to cwd (no copy to databases/)
        db_path = os.path.abspath(db_path)
        ok, result = _validate_and_set_db_path(db_path)
        return result
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'Error loading database: {str(e)}'
        }), 500


@app.route('/database', methods=['GET'])
def database_state():
    """Database state page - displays database statistics"""
    search_conf = load_search_conf()
    if search_conf and 'db_path' in search_conf:
        db_path = search_conf['db_path']
    else:
        db_path = os.path.join(DATABASES_DIR, 'database.db')
    
    db_exists = os.path.exists(db_path)
    db_error = None
    db_stats = None
    iteration_stats = []
    articles_display = []
    
    if db_exists:
        try:
            db_manager = DBManager(db_path)
            
            # Get all iteration data
            all_articles = db_manager.get_iteration_data()
            
            # Calculate statistics
            total_articles = len(all_articles)
            
            # Group by iteration
            by_iteration = defaultdict(int)
            by_selection_stage = defaultdict(int)
            iteration_max = 0
            
            for article in all_articles:
                # Convert iteration to int (it might be stored as string in DB)
                iter_num = 0
                if hasattr(article, 'iteration'):
                    try:
                        iter_num = int(article.iteration) if article.iteration else 0
                    except (ValueError, TypeError):
                        iter_num = 0
                
                by_iteration[iter_num] += 1
                iteration_max = max(iteration_max, iter_num)
                
                # Convert selected to int (it might be stored as string in DB)
                selected = 0
                if hasattr(article, 'selected'):
                    try:
                        selected = int(article.selected) if article.selected is not None else 0
                    except (ValueError, TypeError):
                        selected = 0
                
                by_selection_stage[selected] += 1
            
            # Get iteration statistics
            for iter_num in range(iteration_max + 1):
                # Filter articles for this iteration, converting to int for comparison
                iter_articles = []
                for a in all_articles:
                    if hasattr(a, 'iteration'):
                        try:
                            article_iter = int(a.iteration) if a.iteration else 0
                            if article_iter == iter_num:
                                iter_articles.append(a)
                        except (ValueError, TypeError):
                            continue
                
                iter_count = len(iter_articles)
                
                if iter_count > 0:
                    # Count by selection stage for this iteration
                    iter_selection_counts = defaultdict(int)
                    for article in iter_articles:
                        selected = 0
                        if hasattr(article, 'selected'):
                            try:
                                selected = int(article.selected) if article.selected is not None else 0
                            except (ValueError, TypeError):
                                selected = 0
                        iter_selection_counts[selected] += 1
                    
                    iteration_stats.append({
                        'iteration': iter_num,
                        'total': iter_count,
                        'not_selected': iter_selection_counts.get(0, 0),
                        'metadata_approved': iter_selection_counts.get(1, 0),
                        'title_approved': iter_selection_counts.get(2, 0),
                        'content_approved': iter_selection_counts.get(3, 0),
                        'duplicate': iter_selection_counts.get(-1, 0)
                    })
            
            # Try to get current iteration info
            try:
                result = db_manager.check_current_iteration()
                if result:
                    current_iteration, max_selected, search_method = result
                    if current_iteration is None:
                        current_iteration = iteration_max if iteration_max >= 0 else None
                else:
                    current_iteration = iteration_max if iteration_max >= 0 else None
                    max_selected = None
                    search_method = None
            except:
                current_iteration = iteration_max if iteration_max >= 0 else None
                max_selected = None
                search_method = None
            
            # Get seen titles count
            try:
                seen_titles = db_manager.get_seen_titles_data()
                seen_titles_count = len(seen_titles) if seen_titles else 0
            except:
                seen_titles_count = 0
            
            db_stats = {
                'total_articles': total_articles,
                'total_iterations': iteration_max + 1 if iteration_max >= 0 else 0,
                'current_iteration': current_iteration,
                'search_method': search_method,
                'seen_titles_count': seen_titles_count,
                'by_selection_stage': dict(by_selection_stage)
            }
            
            # Prepare articles for display (limit to first 1000 for performance)
            articles_display = []
            for article in all_articles[:1000]:  # Limit for performance
                try:
                    iter_num = int(article.iteration) if article.iteration else 0
                except (ValueError, TypeError):
                    iter_num = 0
                
                try:
                    selected = int(article.selected) if article.selected is not None else 0
                except (ValueError, TypeError):
                    selected = 0
                
                articles_display.append({
                    'id': getattr(article, 'id', ''),
                    'title': getattr(article, 'title', '') or '(No title)',
                    'authors': getattr(article, 'authors', '') or '(No authors)',
                    'venue': getattr(article, 'venue', '') or '(No venue)',
                    'pub_year': getattr(article, 'pub_year', '') or '',
                    'iteration': iter_num,
                    'selected': selected,
                    'eprint_url': getattr(article, 'eprint_url', ''),
                    'num_citations': getattr(article, 'num_citations', '') or 0
                })
            
        except Exception as e:
            db_error = str(e)
            articles_display = []
    
    return render_template('database_state.html',
                         db_exists=db_exists,
                         db_path=db_path,
                         db_stats=db_stats,
                         iteration_stats=iteration_stats,
                         articles=articles_display,
                         db_error=db_error,
                         SelectionStage=SelectionStage)


@app.route('/api/database/screening_raters', methods=['GET'])
def api_screening_raters():
    """Return distinct rater names from the screening table."""
    try:
        search_conf = load_search_conf()
        if not search_conf or 'db_path' not in search_conf:
            return jsonify({'success': False, 'error': 'Database not configured'}), 400
        db_path = search_conf.get('db_path')
        if not os.path.exists(db_path):
            return jsonify({'success': False, 'error': 'Database file not found'}), 404
        db_manager = DBManager(db_path)
        try:
            raters = db_manager.get_screening_raters()
        except Exception:
            raters = []  # e.g. screening table does not exist
        return jsonify({'success': True, 'raters': raters})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/database/screening_rows', methods=['GET'])
def api_screening_rows():
    """Return screening table rows for a given rater (optional iteration filter)."""
    try:
        search_conf = load_search_conf()
        if not search_conf or 'db_path' not in search_conf:
            return jsonify({'success': False, 'error': 'Database not configured'}), 400
        db_path = search_conf.get('db_path')
        if not os.path.exists(db_path):
            return jsonify({'success': False, 'error': 'Database file not found'}), 404
        rater = request.args.get('rater', '').strip()
        if not rater:
            return jsonify({'success': False, 'error': 'Rater is required'}), 400
        iteration = request.args.get('iteration', type=int)
        db_manager = DBManager(db_path)
        try:
            rows = db_manager.get_screening_rows_by_rater(rater, iteration=iteration)
        except Exception:
            rows = []
        return jsonify({'success': True, 'rows': rows})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/database/annotations_table', methods=['GET'])
def api_annotations_table():
    """Return all rows from the annotations table."""
    try:
        search_conf = load_search_conf()
        if not search_conf or 'db_path' not in search_conf:
            return jsonify({'success': False, 'error': 'Database not configured'}), 400
        db_path = search_conf.get('db_path')
        if not os.path.exists(db_path):
            return jsonify({'success': False, 'error': 'Database file not found'}), 404
        db_manager = DBManager(db_path)
        try:
            rows = db_manager.get_all_annotations_data_with_titles()
        except Exception:
            try:
                rows = db_manager.get_all_annotations_data()
            except Exception:
                rows = []  # e.g. annotations table does not exist
        return jsonify({'success': True, 'rows': rows})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/workflow', methods=['GET'])
def workflow_stage():
    """Workflow stage page - shows current workflow progress"""
    search_conf = load_search_conf()
    
    # Get database path
    if search_conf and 'db_path' in search_conf:
        db_path = search_conf['db_path']
    else:
        db_path = os.path.join(DATABASES_DIR, 'database.db')
    
    db_exists = os.path.exists(db_path)
    workflow_info = get_workflow_info()
    db_error = workflow_info.get('error') if workflow_info else None
    
    return render_template('workflow_stage.html',
                         search_conf=search_conf,
                         db_exists=db_exists,
                         db_path=db_path,
                         workflow_info=workflow_info,
                         db_error=db_error)


@app.route('/api/search_conf', methods=['GET'])
def get_search_conf():
    """API endpoint to get current search configuration"""
    if not os.path.exists(SEARCH_CONF_PATH):
        return jsonify({'error': 'search_conf.json not found'}), 404
    
    try:
        with open(SEARCH_CONF_PATH, 'r') as f:
            config = json.load(f)
        return jsonify(config)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


def allowed_file(filename):
    """Check if file extension is allowed"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


@app.route('/workflow/generate_snowball_start', methods=['GET'])
def generate_snowball_start_page():
    """Page for Step 0: Generate Snowball Start"""
    search_conf = load_search_conf()
    
    # Get defaults from config
    default_input_file = search_conf.get('initial_file', '') if search_conf else ''
    default_search_method = search_conf.get('search_method', 'google_scholar') if search_conf else 'google_scholar'
    default_delay = search_conf.get('request_delay') if search_conf and search_conf.get('request_delay') is not None else (
        2.5 if default_search_method == 'semantic_scholar' else 1.0
    )
    
    task_state = running_tasks['generate_snowball_start']
    workflow_info = get_workflow_info()
    
    return render_template('generate_snowball_start.html',
                         default_input_file=default_input_file,
                         default_delay=default_delay,
                         default_search_method=default_search_method,
                         is_running=task_state['is_running'],
                         logs=task_state['logs'][-50:],  # Last 50 log entries
                         workflow_info=workflow_info)


@app.route('/api/workflow/generate_snowball_start/execute', methods=['POST'])
def execute_generate_snowball_start():
    """Execute the generate snowball start process"""
    task_state = running_tasks['generate_snowball_start']
    
    if task_state['is_running']:
        return jsonify({'error': 'Task is already running'}), 400
    
    try:
        # Get form data
        data = request.get_json() if request.is_json else request.form
        
        input_file = data.get('input_file', '').strip()
        delay = float(data.get('delay', 1.0))
        search_method_str = data.get('search_method', 'google_scholar')
        
        # Validate search method
        try:
            search_method = SearchMethod(search_method_str)
        except ValueError:
            return jsonify({'error': f'Invalid search method: {search_method_str}'}), 400
        
        # Validate input file
        if not input_file:
            return jsonify({'error': 'Input file is required'}), 400
        
        if not os.path.exists(input_file):
            return jsonify({'error': f'Input file does not exist: {input_file}'}), 400
        
        # Get database path from config
        search_conf = load_search_conf()
        if not search_conf or 'db_path' not in search_conf:
            return jsonify({'error': 'Database path not configured. Please set up configuration first.'}), 400
        
        db_path = search_conf['db_path']
        
        # Initialize task state
        task_state['is_running'] = True
        task_state['progress'] = 0
        task_state['total'] = 0
        task_state['current_step'] = 'Starting...'
        task_state['logs'] = []
        cancel_flag = threading.Event()
        task_state['cancel_flag'] = cancel_flag
        
        # Start worker thread
        def worker():
            try:
                # Helper functions for callbacks
                def log(msg):
                    task_state['logs'].append(msg)
                    if len(task_state['logs']) > 1000:  # Limit log size
                        task_state['logs'] = task_state['logs'][-1000:]
                
                def progress(current, total):
                    task_state['progress'] = current
                    task_state['total'] = total
                
                log("Initializing database...")
                # Ensure database directory exists
                db_dir = os.path.dirname(db_path)
                if db_dir and not os.path.exists(db_dir):
                    os.makedirs(db_dir, exist_ok=True)
                db_manager = initialize_db(db_path, search_conf)
                
                log(f"Starting snowball start generation...")
                log(f"Input file: {input_file}")
                log(f"Search method: {search_method_str}")
                log(f"Delay: {delay} seconds")
                if search_method_str == 'semantic_scholar':
                    if _get_semantic_scholar_api_key():
                        log("Semantic Scholar API key detected (S2_API_KEY)")
                    else:
                        log("No Semantic Scholar API key — set S2_API_KEY for better rate limits")
                
                # Execute the generation (returns initial_pubs, seen_titles)
                result = generate_snowball_start(
                    input_file=input_file,
                    iteration=ITERATION_0, 
                    delay=delay,
                    search_method=search_method,
                    progress_callback=progress
                )
                # Handle case where no titles found (function returns None)
                if result is None:
                    log("No titles found in the input file.")
                    return
                
                initial_pubs, seen_titles = result
                
                # Insert data into database
                log(f"Inserting {len(initial_pubs)} publications into database...")
                db_manager.insert_iteration_data(initial_pubs)
                db_manager.insert_seen_titles_data(seen_titles)
                
                if not cancel_flag.is_set():
                    log("✓ Generation completed successfully!")
                    # Update current iteration to 0
                    update_current_iteration(ITERATION_0)
                    # Update workflow state
                    update_workflow_state(
                        db_path=db_path,
                        current_iteration=ITERATION_0,
                        last_step="Step 0: Generate Snowball Start"
                    )
                
            except Exception as e:
                task_state['logs'].append(f"Error: {str(e)}")
                import traceback
                task_state['logs'].append(traceback.format_exc())
            finally:
                task_state['is_running'] = False
                task_state['current_step'] = 'Completed' if not cancel_flag.is_set() else 'Cancelled'
                task_state['cancel_flag'] = None
        
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        
        return jsonify({'success': True, 'message': 'Generation started'})
        
    except Exception as e:
        task_state['is_running'] = False
        return jsonify({'error': str(e)}), 500


@app.route('/api/workflow/generate_snowball_start/status', methods=['GET'])
def get_generate_snowball_start_status():
    """Get status of generate snowball start task"""
    task_state = running_tasks['generate_snowball_start']
    return jsonify({
        'is_running': task_state['is_running'],
        'progress': task_state['progress'],
        'total': task_state['total'],
        'current_step': task_state['current_step'],
        'logs': task_state['logs'][-100:]  # Last 100 log entries
    })


@app.route('/api/workflow/generate_snowball_start/cancel', methods=['POST'])
def cancel_generate_snowball_start():
    """Cancel the running generate snowball start task"""
    task_state = running_tasks['generate_snowball_start']
    
    if not task_state['is_running']:
        return jsonify({'error': 'No task is running'}), 400
    
    if task_state['cancel_flag']:
        task_state['cancel_flag'].set()
        task_state['logs'].append("Cancellation requested...")
        return jsonify({'success': True, 'message': 'Cancellation requested'})
    
    return jsonify({'error': 'No cancel flag available'}), 400


@app.route('/workflow/start_iteration', methods=['GET'])
def start_iteration_page():
    """Page for Step 1: Start Iteration"""
    search_conf = load_search_conf()
    
    # Get defaults from config
    default_search_method = search_conf.get('search_method', 'google_scholar') if search_conf else 'google_scholar'
    
    # Get workflow info
    workflow_info = get_workflow_info()
    current_iteration = workflow_info.get('current_iteration') if workflow_info else None
    
    # Default to iteration 1 if no current iteration
    default_iteration = (current_iteration + 1) if current_iteration is not None else 1
    default_snowball_direction = search_conf.get('snowball_direction', 'both') if search_conf else 'both'

    task_state = running_tasks['start_iteration']

    return render_template('start_iteration.html',
                         default_iteration=default_iteration,
                         default_search_method=default_search_method,
                         default_snowball_direction=default_snowball_direction,
                         is_running=task_state['is_running'],
                         logs=task_state['logs'][-50:],
                         workflow_info=workflow_info)


@app.route('/api/workflow/start_iteration/execute', methods=['POST'])
def execute_start_iteration():
    """Execute the start iteration process"""
    task_state = running_tasks['start_iteration']
    
    if task_state['is_running']:
        return jsonify({'error': 'Task is already running'}), 400
    
    try:
        # Get form data
        data = request.get_json() if request.is_json else request.form
        
        iteration = int(data.get('iteration'))
        search_method_str = data.get('search_method', 'google_scholar')
        snowball_direction = data.get('snowball_direction', 'both')

        if snowball_direction not in SNOWBALL_DIRECTIONS:
            return jsonify({'error': f'Invalid snowball direction: {snowball_direction}'}), 400
        
        # Validate search method
        try:
            search_method = SearchMethod(search_method_str)
        except ValueError:
            return jsonify({'error': f'Invalid search method: {search_method_str}'}), 400
        
        # Validate iteration
        if iteration < 1:
            return jsonify({'error': 'Iteration must be >= 1'}), 400
        
        # Get database path from config
        search_conf = load_search_conf()
        if not search_conf or 'db_path' not in search_conf:
            return jsonify({'error': 'Database path not configured. Please set up configuration first.'}), 400
        
        db_path = search_conf['db_path']
        
        # Initialize task state
        task_state['is_running'] = True
        task_state['progress'] = 0
        task_state['total'] = 0
        task_state['current_step'] = 'Starting...'
        task_state['logs'] = []
        cancel_flag = threading.Event()
        task_state['cancel_flag'] = cancel_flag
        
        # Start worker thread
        def worker():
            try:
                # Helper functions for callbacks
                def log(msg):
                    task_state['logs'].append(msg)
                    if len(task_state['logs']) > 1000:  # Limit log size
                        task_state['logs'] = task_state['logs'][-1000:]
                
                def progress(current, total):
                    task_state['progress'] = current
                    task_state['total'] = total
                
                log("Initializing database...")
                db_manager = DBManager(db_path)
                
                log(f"Starting iteration {iteration}...")
                log(f"Search method: {search_method_str}")
                log(f"Snowball direction: {snowball_direction}")
                direction_kwargs = snowball_direction_kwargs(snowball_direction)
                
                # Get seed publications from previous iteration
                log(f"Fetching seed publications from iteration {iteration - 1}...")
                initial_pubs = db_manager.get_iteration_data(
                    iteration=iteration - 1,
                    selected=SelectionStage.CONTENT_APPROVED,
                    search_method=search_method_str
                )
                
                if len(initial_pubs) == 0:
                    log("✗ No seed publications found!")
                    log("Possible reasons:")
                    log(f"  1. No publications found for search method: {search_method_str}")
                    log(f"  2. No publications found for iteration: {iteration - 1}")
                    log("  3. No publications are marked as CONTENT_APPROVED")
                    task_state['is_running'] = False
                    task_state['current_step'] = 'Failed: No seed publications'
                    return
                
                log(f"Found {len(initial_pubs)} seed publications")
                task_state['total'] = len(initial_pubs)
                
                # Create search instance
                search_method_instance = search_method.create_instance()
                article_search = ArticleSearch(search_method_instance)
                
                # Process articles with progress tracking
                processed = 0
                for i, initial_pub in enumerate(initial_pubs):
                    if cancel_flag.is_set():
                        log("Operation cancelled by user.")
                        return
                    
                    processed = i + 1
                    task_state['progress'] = processed
                    log(f"Processing [{processed}/{len(initial_pubs)}]: {initial_pub.title[:60] if initial_pub.title else 'Unknown'}...")
                    
                    citedby = initial_pub.id
                    try:
                        # Each result is an (ArticleData, direction) tuple where
                        # direction is 'citation' or 'reference'.
                        results = article_search.get_snowballing_articles(
                            citedby,
                            iteration=iteration,
                            **direction_kwargs
                        )
                    except Exception as e:
                        log(f"  ✗ Error fetching articles: {str(e)}")
                        continue

                    if len(results) == 0:
                        log(f"  No articles found")
                        continue

                    log(f"  Found {len(results)} articles")

                    # Record provenance edges for every discovered article
                    # (including duplicates not re-inserted as nodes) so the
                    # citation/reference graph keeps all parent -> child links.
                    edges = [
                        (initial_pub.id, article.id, direction, iteration)
                        for article, direction in results
                        if initial_pub.id and article.id
                    ]
                    db_manager.insert_snowball_edges(edges)

                    # Filter out already seen articles
                    filtered_articles = [
                        article for article, _direction in results
                        if db_manager.get_seen_title(article.title) is None
                    ]

                    log(f"  {len(filtered_articles)} new articles")

                    if filtered_articles:
                        db_manager.insert_iteration_data(filtered_articles)
                        db_manager.insert_seen_titles_data(
                            [(article.title, article.id) for article in filtered_articles]
                        )
                        log(f"  ✓ Saved to database")
                
                if not cancel_flag.is_set():
                    log(f"✓ Successfully completed iteration {iteration}")
                    
                    # Check for articles without IDs
                    articles_no_id = db_manager.get_iteration_data(
                        iteration=iteration,
                        id__empty=True
                    )
                    if articles_no_id:
                        log(f"⚠ Warning: Found {len(articles_no_id)} articles without IDs")
                        task_state['articles_without_id_count'] = len(articles_no_id)
                        task_state['articles_without_id_iteration'] = iteration
                    else:
                        task_state['articles_without_id_count'] = 0
                        task_state['articles_without_id_iteration'] = None
                    
                    # Update current iteration
                    update_current_iteration(iteration)
                    # Update workflow state
                    update_workflow_state(
                        db_path=db_path,
                        current_iteration=iteration,
                        last_step="Step 1: Start Iteration"
                    )
                    db_manager.conn.close()
                
            except Exception as e:
                task_state['logs'].append(f"Error: {str(e)}")
                import traceback
                task_state['logs'].append(traceback.format_exc())
            finally:
                task_state['is_running'] = False
                task_state['current_step'] = 'Completed' if not cancel_flag.is_set() else 'Cancelled'
                task_state['cancel_flag'] = None
        
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        
        return jsonify({'success': True, 'message': 'Iteration started'})
        
    except Exception as e:
        task_state['is_running'] = False
        return jsonify({'error': str(e)}), 500


@app.route('/api/workflow/start_iteration/status', methods=['GET'])
def get_start_iteration_status():
    """Get status of start iteration task"""
    task_state = running_tasks['start_iteration']
    return jsonify({
        'is_running': task_state['is_running'],
        'progress': task_state['progress'],
        'total': task_state['total'],
        'current_step': task_state['current_step'],
        'logs': task_state['logs'][-100:],
        'articles_without_id_count': task_state.get('articles_without_id_count', 0),
        'articles_without_id_iteration': task_state.get('articles_without_id_iteration', None)
    })


@app.route('/api/workflow/start_iteration/cancel', methods=['POST'])
def cancel_start_iteration():
    """Cancel the running start iteration task"""
    task_state = running_tasks['start_iteration']
    
    if not task_state['is_running']:
        return jsonify({'error': 'No task is running'}), 400
    
    if task_state['cancel_flag']:
        task_state['cancel_flag'].set()
        task_state['logs'].append("Cancellation requested...")
        return jsonify({'success': True, 'message': 'Cancellation requested'})
    
    return jsonify({'error': 'No cancel flag available'}), 400


@app.route('/workflow/generate_citation_graph', methods=['GET'])
def generate_citation_graph_page():
    """Page for generating the citation/reference graph."""
    workflow_info = get_workflow_info()
    task_state = running_tasks['generate_citation_graph']
    return render_template('generate_citation_graph.html',
                           is_running=task_state['is_running'],
                           logs=task_state['logs'][-50:],
                           outputs=task_state.get('outputs', {}),
                           workflow_info=workflow_info)


@app.route('/api/workflow/generate_citation_graph/execute', methods=['POST'])
def execute_generate_citation_graph():
    """Build the citation/reference graph and write output files."""
    task_state = running_tasks['generate_citation_graph']

    if task_state['is_running']:
        return jsonify({'error': 'Task is already running'}), 400

    try:
        data = request.get_json() if request.is_json else request.form
        edge_source = data.get('edge_source', 'both')
        use_physics = bool(data.get('use_physics', False))
        accepted_only = bool(data.get('accepted_only', False))
        use_citations = bool(data.get('use_citations', True))
        augment_openalex = bool(data.get('augment_openalex', False))

        if edge_source not in EDGE_SOURCES:
            return jsonify({'error': f'Invalid edge source: {edge_source}'}), 400

        search_conf = load_search_conf()
        if not search_conf or 'db_path' not in search_conf:
            return jsonify({'error': 'Database path not configured. Please set up configuration first.'}), 400
        db_path = search_conf['db_path']
        if not os.path.exists(db_path):
            return jsonify({'error': f'Database not found: {db_path}'}), 400

        # Name outputs after the database so multiple DBs do not collide, with
        # an "_accepted" suffix when restricted to content-approved papers (the
        # reference cache is shared because the orchestrator strips that suffix).
        basename = os.path.splitext(os.path.basename(db_path))[0] + "_graph"
        if accepted_only:
            basename += "_accepted"

        task_state['is_running'] = True
        task_state['progress'] = 0
        task_state['total'] = 0
        task_state['current_step'] = 'Starting...'
        task_state['logs'] = []
        task_state['outputs'] = {}
        cancel_flag = threading.Event()
        task_state['cancel_flag'] = cancel_flag

        def worker():
            try:
                def log(msg):
                    task_state['logs'].append(str(msg))
                    if len(task_state['logs']) > 1000:
                        task_state['logs'] = task_state['logs'][-1000:]

                os.makedirs(GRAPHS_DIR, exist_ok=True)
                log(f"Database: {db_path}")
                log(f"Edge source: {edge_source}")
                if accepted_only:
                    log("Scope: content-approved (accepted) papers only.")
                if edge_source in ('api', 'both'):
                    log("Note: API reconstruction queries Semantic Scholar once per "
                        "paper; this can take several minutes (results are cached).")

                outputs = generate_citation_graph(
                    db_path=db_path,
                    output_dir=GRAPHS_DIR,
                    edge_source=edge_source,
                    formats=DEFAULT_FORMATS,
                    basename=basename,
                    accepted_only=accepted_only,
                    use_citations=use_citations,
                    augment_openalex=augment_openalex,
                    use_physics=use_physics,
                    log=log,
                    cancel_flag=cancel_flag,
                )

                # Expose web paths for the UI (files live under static/).
                web_outputs = {}
                for fmt, path in outputs.items():
                    web_outputs[fmt] = '/' + path.replace(os.sep, '/')
                task_state['outputs'] = web_outputs
                if not cancel_flag.is_set():
                    log("✓ Graph generation complete.")
            except Exception as e:
                task_state['logs'].append(f"Error: {str(e)}")
                import traceback
                task_state['logs'].append(traceback.format_exc())
            finally:
                task_state['is_running'] = False
                task_state['current_step'] = 'Completed' if not cancel_flag.is_set() else 'Cancelled'
                task_state['cancel_flag'] = None

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

        return jsonify({'success': True, 'message': 'Graph generation started'})

    except Exception as e:
        task_state['is_running'] = False
        return jsonify({'error': str(e)}), 500


@app.route('/api/workflow/generate_citation_graph/status', methods=['GET'])
def get_generate_citation_graph_status():
    """Get status of the citation graph task."""
    task_state = running_tasks['generate_citation_graph']
    return jsonify({
        'is_running': task_state['is_running'],
        'progress': task_state['progress'],
        'total': task_state['total'],
        'current_step': task_state['current_step'],
        'logs': task_state['logs'][-100:],
        'outputs': task_state.get('outputs', {}),
    })


@app.route('/api/workflow/generate_citation_graph/cancel', methods=['POST'])
def cancel_generate_citation_graph():
    """Cancel the running citation graph task."""
    task_state = running_tasks['generate_citation_graph']
    if not task_state['is_running']:
        return jsonify({'error': 'No task is running'}), 400
    if task_state['cancel_flag']:
        task_state['cancel_flag'].set()
        task_state['logs'].append("Cancellation requested...")
        return jsonify({'success': True, 'message': 'Cancellation requested'})
    return jsonify({'error': 'No cancel flag available'}), 400


# ---------------------------------------------------------------------------
# Cluster explorer: navigate per-topic citation graphs with a details panel
# ---------------------------------------------------------------------------

def _load_clusters_manifest():
    """Return the parsed cluster manifest dict, or None if not generated yet."""
    path = os.path.join(CLUSTERS_DIR, MANIFEST_NAME)
    if not os.path.exists(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def _cluster_by_code(manifest, code):
    for c in (manifest or {}).get('clusters', []):
        if c.get('code') == code:
            return c
    return None


@app.route('/clusters', methods=['GET'])
def cluster_explorer_page():
    """Interactive explorer to navigate per-cluster citation graphs."""
    workflow_info = get_workflow_info()
    manifest = _load_clusters_manifest()
    task_state = running_tasks['generate_cluster_graphs']
    return render_template(
        'cluster_explorer.html',
        has_clusters=bool(manifest and manifest.get('clusters')),
        generated_at=(manifest or {}).get('generated_at', ''),
        is_running=task_state['is_running'],
        logs=task_state['logs'][-50:],
        workflow_info=workflow_info,
    )


@app.route('/api/clusters', methods=['GET'])
def api_clusters_list():
    """Return the list of available clusters (from the manifest)."""
    manifest = _load_clusters_manifest()
    if not manifest:
        return jsonify({'clusters': [], 'generated_at': ''})
    # Only expose the lightweight fields the navigator needs.
    clusters = [{
        'code': c.get('code'),
        'label': c.get('label'),
        'depth': c.get('depth'),
        'nodes': c.get('nodes'),
        'edges': c.get('edges'),
        'missing_papers': c.get('missing_papers', 0),
    } for c in manifest.get('clusters', [])]
    return jsonify({'clusters': clusters, 'generated_at': manifest.get('generated_at', '')})


@app.route('/api/clusters/<code>', methods=['GET'])
def api_cluster_graph(code):
    """Return the nodes/edges JSON for a single cluster."""
    manifest = _load_clusters_manifest()
    c = _cluster_by_code(manifest, code)
    if not c:
        return jsonify({'error': f'Unknown cluster: {code}'}), 404
    json_name = c.get('files', {}).get('json')
    if not json_name:
        return jsonify({'error': f'No JSON graph for cluster {code}'}), 404
    path = os.path.join(CLUSTERS_DIR, json_name)
    if not os.path.exists(path):
        return jsonify({'error': f'Graph file missing: {json_name}'}), 404
    with open(path, 'r', encoding='utf-8') as f:
        graph = json.load(f)
    return jsonify({
        'code': c.get('code'),
        'label': c.get('label'),
        'full_label': c.get('full_label'),
        'nodes': graph.get('nodes', []),
        'edges': graph.get('edges', []),
    })


_search_index = {'key': None, 'items': None}


def _authors_to_str(au):
    """Normalise an iterations.authors value to a comma-separated name string."""
    try:
        data = json.loads(au) if isinstance(au, str) else au
        if isinstance(data, list):
            names = [x.get('name') if isinstance(x, dict) else str(x) for x in data]
            return ', '.join(n for n in names if n)
    except Exception:
        pass
    return au if isinstance(au, str) else ''


def _build_search_index(manifest):
    """One entry per paper appearing in any cluster graph:
    {id, title, year, authors, codes:[cluster codes]}. Authors come from the
    databases (not stored in the graph), matched by paper id."""
    byid = {}
    for c in manifest.get('clusters', []):
        code = c.get('code')
        jn = c.get('files', {}).get('json')
        if not jn:
            continue
        p = os.path.join(CLUSTERS_DIR, jn)
        if not os.path.exists(p):
            continue
        try:
            with open(p, 'r', encoding='utf-8') as f:
                g = json.load(f)
        except Exception:
            continue
        for n in g.get('nodes', []):
            e = byid.setdefault(n['id'], {
                'id': n['id'], 'title': n.get('title', ''),
                'year': n.get('year', ''), 'codes': set()})
            e['codes'].add(code)
    # Authors from whichever database holds the id (prefer first non-empty).
    authors = {}
    for db in sorted(glob.glob(os.path.join('databases', '*.db'))):
        try:
            con = sqlite3.connect(db)
            rows = con.execute('select id, authors from iterations').fetchall()
            con.close()
        except Exception:
            continue
        for pid, au in rows:
            if pid in byid and not authors.get(pid):
                s = _authors_to_str(au)
                if s:
                    authors[pid] = s
    items = []
    for pid, e in byid.items():
        e['authors'] = authors.get(pid, '')
        e['codes'] = sorted(e['codes'])
        items.append(e)
    return items


def _search_score(q, title, authors):
    """Fuzzy relevance of a paper to query ``q`` over title and authors."""
    import re as _re
    import difflib
    ql = q.lower().strip()
    if not ql:
        return 0.0
    t = (title or '').lower()
    a = (authors or '').lower()
    sc = 0.0
    if ql in t:
        sc = max(sc, 0.95)
    if ql in a:
        sc = max(sc, 0.88)
    qtok = set(_re.findall(r'[a-z0-9]+', ql))
    if qtok:
        ttok = set(_re.findall(r'[a-z0-9]+', t))
        atok = set(_re.findall(r'[a-z0-9]+', a))
        sc = max(sc, 0.7 * len(qtok & ttok) / len(qtok))
        sc = max(sc, 0.7 * len(qtok & atok) / len(qtok))
    sc = max(sc, 0.72 * difflib.SequenceMatcher(None, ql, t).ratio())
    return sc


@app.route('/api/clusters/search', methods=['GET'])
def api_clusters_search():
    """Fuzzy search papers (by title or author) across all cluster graphs."""
    q = (request.args.get('q') or '').strip()
    manifest = _load_clusters_manifest()
    if not manifest or len(q) < 2:
        return jsonify({'results': []})
    key = manifest.get('generated_at')
    if _search_index['key'] != key or _search_index['items'] is None:
        _search_index['items'] = _build_search_index(manifest)
        _search_index['key'] = key
    scored = []
    for e in _search_index['items']:
        s = _search_score(q, e['title'], e['authors'])
        if s >= 0.3:
            scored.append((s, e))
    scored.sort(key=lambda x: -x[0])
    results = [{
        'id': e['id'], 'title': e['title'], 'authors': e['authors'],
        'year': e['year'], 'codes': e['codes'], 'score': round(s, 3),
    } for s, e in scored[:20]]
    return jsonify({'results': results})


@app.route('/api/clusters/<code>/download/<fmt>', methods=['GET'])
def api_cluster_download(code, fmt):
    """Download a cluster graph file (graphml/gexf/json/html)."""
    manifest = _load_clusters_manifest()
    c = _cluster_by_code(manifest, code)
    if not c:
        return jsonify({'error': f'Unknown cluster: {code}'}), 404
    fname = c.get('files', {}).get(fmt.lower())
    if not fname:
        return jsonify({'error': f'No {fmt} file for cluster {code}'}), 404
    path = os.path.join(CLUSTERS_DIR, fname)
    if not os.path.exists(path):
        return jsonify({'error': f'File missing: {fname}'}), 404
    as_download = fmt.lower() != 'html'
    return send_file(os.path.abspath(path), as_attachment=as_download)


@app.route('/api/clusters/execute', methods=['POST'])
def execute_generate_cluster_graphs():
    """(Re)generate per-cluster graphs by slicing the full accepted graph."""
    task_state = running_tasks['generate_cluster_graphs']
    if task_state['is_running']:
        return jsonify({'error': 'Task is already running'}), 400

    try:
        data = request.get_json() if request.is_json else request.form
        cluster_file = data.get('cluster_file', 'cluster_keywords.json')
        code_prefix = data.get('code_prefix', 'T')

        if not os.path.exists(cluster_file):
            return jsonify({'error': f'Cluster file not found: {cluster_file}'}), 400

        search_conf = load_search_conf()
        if not search_conf or 'db_path' not in search_conf:
            return jsonify({'error': 'Database path not configured.'}), 400
        db_path = search_conf['db_path']

        # Prefer slicing an already-generated accepted graph (no API calls).
        candidates = glob.glob(os.path.join('graphs', '*accepted*.graphml')) + \
            glob.glob(os.path.join(GRAPHS_DIR, '*accepted*.graphml'))
        source_graph = max(candidates, key=os.path.getmtime) if candidates else None

        task_state.update({'is_running': True, 'progress': 0, 'total': 0,
                           'current_step': 'Starting...', 'logs': [], 'outputs': {}})
        cancel_flag = threading.Event()
        task_state['cancel_flag'] = cancel_flag

        def worker():
            try:
                def log(msg):
                    task_state['logs'].append(str(msg))
                    if len(task_state['logs']) > 1000:
                        task_state['logs'] = task_state['logs'][-1000:]

                os.makedirs(CLUSTERS_DIR, exist_ok=True)
                if source_graph:
                    log(f"Slicing existing graph: {source_graph}")
                else:
                    log("No accepted graph found; will rebuild from the database "
                        "(this queries Semantic Scholar/OpenAlex and is slow).")

                results = generate_cluster_graphs(
                    db_path=db_path,
                    cluster_file=cluster_file,
                    output_dir=CLUSTERS_DIR,
                    code_prefix=code_prefix,
                    source_graph_path=source_graph,
                    log=log,
                    cancel_flag=cancel_flag,
                )
                task_state['outputs'] = {'count': len(results)}
                if not cancel_flag.is_set():
                    log(f"✓ Generated {len(results)} cluster graphs.")
            except Exception as e:
                task_state['logs'].append(f"Error: {str(e)}")
                import traceback
                task_state['logs'].append(traceback.format_exc())
            finally:
                task_state['is_running'] = False
                task_state['current_step'] = 'Completed' if not cancel_flag.is_set() else 'Cancelled'
                task_state['cancel_flag'] = None

        threading.Thread(target=worker, daemon=True).start()
        return jsonify({'success': True, 'message': 'Cluster graph generation started'})

    except Exception as e:
        task_state['is_running'] = False
        return jsonify({'error': str(e)}), 500


@app.route('/api/clusters/execute/status', methods=['GET'])
def get_generate_cluster_graphs_status():
    task_state = running_tasks['generate_cluster_graphs']
    return jsonify({
        'is_running': task_state['is_running'],
        'current_step': task_state['current_step'],
        'logs': task_state['logs'][-100:],
        'outputs': task_state.get('outputs', {}),
    })


@app.route('/api/clusters/execute/cancel', methods=['POST'])
def cancel_generate_cluster_graphs():
    task_state = running_tasks['generate_cluster_graphs']
    if not task_state['is_running']:
        return jsonify({'error': 'No task is running'}), 400
    if task_state['cancel_flag']:
        task_state['cancel_flag'].set()
        task_state['logs'].append("Cancellation requested...")
        return jsonify({'success': True, 'message': 'Cancellation requested'})
    return jsonify({'error': 'No cancel flag available'}), 400


def _find_references_cache(pid=None):
    """Return the most suitable reconstructed-references cache, or None.

    Prefers a cache that actually contains ``pid``; otherwise the one with the
    most entries (the comprehensive cache, not a small test run's).
    """
    caches = glob.glob(os.path.join('graphs', '*_references_cache.json')) + \
        glob.glob(os.path.join(GRAPHS_DIR, '*_references_cache.json'))
    best, best_keys = None, -1
    for cp in caches:
        try:
            with open(cp, 'r', encoding='utf-8') as f:
                c = json.load(f)
        except Exception:
            continue
        if pid and pid in c:
            return cp
        if len(c) > best_keys:
            best, best_keys = cp, len(c)
    return best


def _find_accepted_graph(pid=None):
    """Return the accepted-graph GraphML best matching ``pid`` (one that
    contains it), or the largest, or None."""
    cands = glob.glob(os.path.join('graphs', '*accepted*.graphml')) + \
        glob.glob(os.path.join(GRAPHS_DIR, '*accepted*.graphml'))
    best, best_n = None, -1
    for gp in cands:
        try:
            import networkx as nx
            g = nx.read_graphml(gp)
        except Exception:
            continue
        if pid and g.has_node(pid):
            return gp
        if g.number_of_nodes() > best_n:
            best, best_n = gp, g.number_of_nodes()
    return best


@app.route('/api/paper/<pid>/links', methods=['GET'])
def api_paper_links(pid):
    """Return a paper's references and citations for the explorer side panel."""
    search_conf = load_search_conf()
    if not search_conf or 'db_path' not in search_conf:
        return jsonify({'error': 'Database path not configured.'}), 400
    db_path = search_conf['db_path']

    cache_path = _find_references_cache(pid)
    if not cache_path:
        return jsonify({'error': 'No references cache found. Generate the citation '
                                 'graph first so references/citations are available.'}), 404

    cluster_file = request.args.get('cluster_file', 'cluster_keywords.json')
    refresh = request.args.get('refresh', '0') == '1'
    # The accepted graph supplies the true edges (both directions + OpenAlex).
    graph_path = _find_accepted_graph(pid)
    try:
        data = get_paper_links(
            pid, db_path, cluster_file, cache_path,
            graph_path=graph_path, output_dir=CLUSTERS_DIR, refresh=refresh,
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    return jsonify(data)


@app.route('/api/workflow/start_iteration/check_articles_without_id', methods=['GET'])
def check_articles_without_id():
    """Check for articles without IDs in a specific iteration"""
    try:
        iteration = int(request.args.get('iteration', 0))
        if iteration < 1:
            return jsonify({'error': 'Valid iteration number is required'}), 400
        
        # Get database path from config
        search_conf = load_search_conf()
        if not search_conf or 'db_path' not in search_conf:
            return jsonify({'error': 'Database path not configured'}), 400
        
        db_path = search_conf['db_path']
        db_manager = DBManager(db_path)
        
        # Get articles without IDs
        articles_no_id = db_manager.get_iteration_data(
            iteration=iteration,
            id__empty=True
        )
        
        articles_data = []
        for article in articles_no_id:
            articles_data.append({
                'title': article.title or 'No title',
                'venue': article.venue or '',
                'pub_year': str(article.pub_year) if article.pub_year else '',
                'authors': article.authors or ''
            })
        
        return jsonify({
            'success': True,
            'count': len(articles_no_id),
            'articles': articles_data,
            'iteration': iteration
        })
        
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@app.route('/api/workflow/start_iteration/delete_articles_without_id', methods=['POST'])
def delete_articles_without_id():
    """Delete articles without IDs from a specific iteration"""
    try:
        data = request.get_json()
        iteration = int(data.get('iteration', 0))
        
        if iteration < 1:
            return jsonify({'error': 'Valid iteration number is required'}), 400
        
        # Get database path from config
        search_conf = load_search_conf()
        if not search_conf or 'db_path' not in search_conf:
            return jsonify({'error': 'Database path not configured'}), 400
        
        db_path = search_conf['db_path']
        db_manager = DBManager(db_path)
        
        # Get count before deletion
        articles_no_id = db_manager.get_iteration_data(
            iteration=iteration,
            id__empty=True
        )
        deleted_count = len(articles_no_id)
        
        if deleted_count == 0:
            return jsonify({
                'success': True,
                'message': 'No articles without IDs found',
                'deleted_count': 0
            })
        
        # Use the clear_unidentified_articles method
        db_manager.clear_unidentified_articles(iteration)
        
        # Clear the task state if this was the iteration we just processed
        task_state = running_tasks['start_iteration']
        if task_state.get('articles_without_id_iteration') == iteration:
            task_state['articles_without_id_count'] = 0
            task_state['articles_without_id_iteration'] = None
        
        return jsonify({
            'success': True,
            'message': f'Successfully deleted {deleted_count} article(s) without IDs',
            'deleted_count': deleted_count
        })
        
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@app.route('/api/workflow/start_iteration/search_article', methods=['POST'])
def search_article_for_repair():
    """Search for an article by title using different search methods"""
    try:
        data = request.get_json()
        title = data.get('title', '').strip()
        search_methods_str = data.get('search_methods', ['semantic_scholar', 'google_scholar', 'dblp'])
        
        if not title:
            return jsonify({'error': 'Title is required'}), 400
        
        # Initialize search methods
        search_methods_map = {
            'semantic_scholar': SemanticScholarSearchMethod(),
            'google_scholar': GoogleScholarSearchMethod(),
            'dblp': DBLPSearchMethod()
        }
        
        results = []
        for method_name in search_methods_str:
            if method_name not in search_methods_map:
                continue
            
            try:
                search_method = search_methods_map[method_name]
                found_articles = search_method.search(title)
                
                if found_articles and len(found_articles) > 0:
                    article = found_articles[0]
                    results.append({
                        'method': method_name,
                        'article_id': article.id,
                        'title': article.title,
                        'authors': article.authors,
                        'venue': article.venue,
                        'pub_year': article.pub_year,
                        'pub_url': article.pub_url,
                        'found': True
                    })
                    break  # Stop at first successful search
                else:
                    results.append({
                        'method': method_name,
                        'found': False
                    })
            except Exception as e:
                results.append({
                    'method': method_name,
                    'found': False,
                    'error': str(e)
                })
        
        if results and any(r.get('found') for r in results):
            return jsonify({
                'success': True,
                'found': True,
                'results': results
            })
        else:
            return jsonify({
                'success': True,
                'found': False,
                'results': results,
                'message': 'No article found with any search method'
            })
        
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@app.route('/api/workflow/start_iteration/repair_article', methods=['POST'])
def repair_article_id():
    """Update an article's ID after finding it via search"""
    try:
        data = request.get_json()
        iteration = int(data.get('iteration', 0))
        old_title = data.get('title', '').strip()
        new_article_id = data.get('article_id', '').strip()
        
        if iteration < 1:
            return jsonify({'error': 'Valid iteration number is required'}), 400
        
        if not old_title:
            return jsonify({'error': 'Title is required'}), 400
        
        if not new_article_id:
            return jsonify({'error': 'Article ID is required'}), 400
        
        # Get database path from config
        search_conf = load_search_conf()
        if not search_conf or 'db_path' not in search_conf:
            return jsonify({'error': 'Database path not configured'}), 400
        
        db_path = search_conf['db_path']
        db_manager = DBManager(db_path)
        
        # Find the article by title in the iteration (with no ID)
        articles = db_manager.get_iteration_data(
            iteration=iteration,
            id__empty=True
        )
        
        # Find matching article by title
        matching_article = None
        for article in articles:
            if article.title == old_title:
                matching_article = article
                break
        
        if not matching_article:
            return jsonify({'error': f'Article with title "{old_title}" not found in iteration {iteration}'}), 404
        
        # Update the article's ID
        matching_article.id = new_article_id
        db_manager.insert_iteration_data([matching_article])
        
        # Update seen_titles table to link title to new ID
        db_manager.insert_seen_titles_data([(matching_article.title.lower(), new_article_id)])
        
        # Delete the old entry (with empty ID)
        db_manager.cursor.execute(
            "DELETE FROM iterations WHERE id = ? AND iteration = ? AND title = ?",
            ('', iteration, old_title)
        )
        db_manager.conn.commit()
        
        # Clear the task state if this was the iteration we just processed
        task_state = running_tasks['start_iteration']
        if task_state.get('articles_without_id_iteration') == iteration:
            # Re-check count
            remaining = db_manager.get_iteration_data(
                iteration=iteration,
                id__empty=True
            )
            task_state['articles_without_id_count'] = len(remaining)
            if len(remaining) == 0:
                task_state['articles_without_id_iteration'] = None
        
        return jsonify({
            'success': True,
            'message': f'Successfully repaired article: {old_title}',
            'article_id': new_article_id
        })
        
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@app.route('/api/workflow/start_iteration/delete_single_article', methods=['POST'])
def delete_single_article_without_id():
    """Delete a single article without ID from a specific iteration"""
    try:
        data = request.get_json()
        iteration = int(data.get('iteration', 0))
        title = data.get('title', '').strip()
        
        if iteration < 1:
            return jsonify({'error': 'Valid iteration number is required'}), 400
        
        if not title:
            return jsonify({'error': 'Title is required'}), 400
        
        # Get database path from config
        search_conf = load_search_conf()
        if not search_conf or 'db_path' not in search_conf:
            return jsonify({'error': 'Database path not configured'}), 400
        
        db_path = search_conf['db_path']
        db_manager = DBManager(db_path)
        
        # Delete the article by title and iteration (with empty ID)
        db_manager.cursor.execute(
            "DELETE FROM iterations WHERE id = ? AND iteration = ? AND title = ?",
            ('', iteration, title)
        )
        deleted_count = db_manager.cursor.rowcount
        db_manager.conn.commit()
        
        if deleted_count == 0:
            return jsonify({
                'success': False,
                'message': 'Article not found',
                'error': f'Article with title "{title}" not found in iteration {iteration}'
            }), 404
        
        # Clear the task state if this was the iteration we just processed
        task_state = running_tasks['start_iteration']
        if task_state.get('articles_without_id_iteration') == iteration:
            # Re-check count
            remaining = db_manager.get_iteration_data(
                iteration=iteration,
                id__empty=True
            )
            task_state['articles_without_id_count'] = len(remaining)
            if len(remaining) == 0:
                task_state['articles_without_id_iteration'] = None
        
        return jsonify({
            'success': True,
            'message': f'Successfully deleted article: {title}',
            'deleted_count': deleted_count
        })
        
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@app.route('/workflow/get_bibtex', methods=['GET'])
def get_bibtex_page():
    """Page for Step 3: Get BibTeX"""
    search_conf = load_search_conf()
    
    # Get defaults from config
    default_search_method = search_conf.get('search_method', 'google_scholar') if search_conf else 'google_scholar'
    
    # Get workflow info
    workflow_info = get_workflow_info()
    current_iteration = workflow_info.get('current_iteration') if workflow_info else None
    
    # Default to current iteration if available
    default_iteration = current_iteration if current_iteration is not None else 0
    
    task_state = running_tasks['get_bibtex']
    
    return render_template('get_bibtex.html',
                         default_iteration=default_iteration,
                         default_search_method=default_search_method,
                         is_running=task_state['is_running'],
                         logs=task_state['logs'][-50:],
                         workflow_info=workflow_info)


@app.route('/api/workflow/get_bibtex/execute', methods=['POST'])
def execute_get_bibtex():
    """Execute the get bibtex process"""
    task_state = running_tasks['get_bibtex']
    
    if task_state['is_running']:
        return jsonify({'error': 'Task is already running'}), 400
    
    try:
        # Get form data
        data = request.get_json() if request.is_json else request.form
        
        iteration = int(data.get('iteration'))
        search_method_str = data.get('search_method', 'google_scholar')
        delay = float(data.get('delay', 1.0))
        batch_size = int(data.get('batch_size', 10))
        max_workers = int(data.get('max_workers', 3))
        use_parallel = data.get('use_parallel', 'true') == 'true'
        
        # Validate search method (the script expects a string value, not an enum)
        valid_methods = [method.value for method in SearchMethod]
        if search_method_str not in valid_methods:
            return jsonify({'error': f'Invalid search method: {search_method_str}. Valid options: {valid_methods}'}), 400
        
        # Validate iteration
        if iteration < 0:
            return jsonify({'error': 'Iteration must be >= 0'}), 400
        
        # Get database path from config
        search_conf = load_search_conf()
        if not search_conf or 'db_path' not in search_conf:
            return jsonify({'error': 'Database path not configured. Please set up configuration first.'}), 400
        
        db_path = search_conf['db_path']
        
        # Initialize task state
        task_state['is_running'] = True
        task_state['progress'] = 0
        task_state['total'] = 0
        task_state['current_step'] = 'Starting...'
        task_state['logs'] = []
        cancel_flag = threading.Event()
        task_state['cancel_flag'] = cancel_flag
        
        # Start worker thread
        def worker():
            try:
                # Helper functions for callbacks
                def log(msg):
                    task_state['logs'].append(msg)
                    if len(task_state['logs']) > 1000:  # Limit log size
                        task_state['logs'] = task_state['logs'][-1000:]
                
                log("Initializing database...")
                db_manager = DBManager(db_path)
                
                log(f"Fetching articles from iteration {iteration} without bibtex...")
                articles = db_manager.get_iteration_data(
                    iteration=iteration,
                    bibtex__empty=True,
                    selected=SelectionStage.NOT_SELECTED
                )
                
                if len(articles) == 0:
                    log("No articles found that need BibTeX processing.")
                    log("All articles in this iteration already have BibTeX data.")
                    task_state['is_running'] = False
                    task_state['current_step'] = 'Completed: No articles to process'
                    return
                
                log(f"Found {len(articles)} articles without bibtex")
                task_state['total'] = len(articles)
                
                # Process articles with progress tracking
                processed_article_ids = set()  # Track unique article IDs to avoid double counting
                
                # Monkey-patch the process function to track progress
                original_update = db_manager.update_batch_iteration_data
                def tracked_update(iter_num, update_data):
                    # Only count unique articles (by article_id) to avoid triple counting
                    # since update_bibtex_info calls update_batch_iteration_data 3 times
                    for item in update_data:
                        if len(item) >= 1:
                            article_id = item[0]
                            processed_article_ids.add(article_id)
                    task_state['progress'] = len(processed_article_ids)
                    task_state['current_step'] = f'Processed {len(processed_article_ids)}/{len(articles)} articles'
                    return original_update(iter_num, update_data)
                
                db_manager.update_batch_iteration_data = tracked_update
                
                log(f"Starting BibTeX retrieval for {len(articles)} articles...")
                log(f"Search method: {search_method_str}")
                log(f"Delay: {delay} seconds")
                log(f"Batch size: {batch_size}")
                log(f"Parallel processing: {use_parallel}")
                if use_parallel:
                    log(f"Max workers: {max_workers}")
                
                # Execute the processing (pass string value, not enum, and cancel_flag)
                process_articles_optimized(
                    iteration=iteration,
                    articles=articles,
                    db_manager=db_manager,
                    batch_size=batch_size,
                    max_workers=max_workers,
                    use_parallel=use_parallel,
                    search_method=search_method_str,
                    delay=delay,
                    cancel_flag=cancel_flag
                )
                
                if not cancel_flag.is_set():
                    log(f"✓ Successfully processed {len(articles)} articles and saved BibTeX to database.")
                    # Update workflow state
                    # db_path is already available from outer scope
                    current_iter = get_current_iteration_from_db(db_manager, articles)
                    update_workflow_state(
                        db_path=db_path,
                        current_iteration=current_iter,
                        last_step="Step 3: Get BibTeX"
                    )
                
            except Exception as e:
                task_state['logs'].append(f"Error: {str(e)}")
                import traceback
                task_state['logs'].append(traceback.format_exc())
            finally:
                task_state['is_running'] = False
                task_state['current_step'] = 'Completed' if not cancel_flag.is_set() else 'Cancelled'
                task_state['cancel_flag'] = None
        
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        
        return jsonify({'success': True, 'message': 'BibTeX retrieval started'})
        
    except Exception as e:
        task_state['is_running'] = False
        return jsonify({'error': str(e)}), 500


@app.route('/api/workflow/get_bibtex/status', methods=['GET'])
def get_get_bibtex_status():
    """Get status of get bibtex task"""
    task_state = running_tasks['get_bibtex']
    return jsonify({
        'is_running': task_state['is_running'],
        'progress': task_state['progress'],
        'total': task_state['total'],
        'current_step': task_state['current_step'],
        'logs': task_state['logs'][-100:]
    })


@app.route('/api/workflow/get_bibtex/cancel', methods=['POST'])
def cancel_get_bibtex():
    """Cancel the running get bibtex task"""
    task_state = running_tasks['get_bibtex']
    
    if not task_state['is_running']:
        return jsonify({'error': 'No task is running'}), 400
    
    if task_state['cancel_flag']:
        task_state['cancel_flag'].set()
        task_state['logs'].append("Cancellation requested...")
        return jsonify({'success': True, 'message': 'Cancellation requested'})
    
    return jsonify({'error': 'No cancel flag available'}), 400


@app.route('/workflow/generate_conf_rank', methods=['GET'])
def generate_conf_rank_page():
    """Page for Step 4: Generate Conf Rank"""
    search_conf = load_search_conf()
    
    # Get workflow info
    workflow_info = get_workflow_info()
    current_iteration = workflow_info.get('current_iteration') if workflow_info else None
    
    # Default to current iteration if available
    default_iteration = current_iteration if current_iteration is not None else 0
    
    # Get valid venue ranks from config
    venue_ranks = search_conf.get('venue_rank_list', ['A*', 'A', 'B', 'C', 'D', 'Q1', 'Q2', 'Q3', 'Q4', 'NA']) if search_conf else ['A*', 'A', 'B', 'C', 'D', 'Q1', 'Q2', 'Q3', 'Q4', 'NA']
    # Ensure "NA" is always available as an option
    if 'NA' not in venue_ranks:
        venue_ranks.append('NA')
    
    # Check if BibTeX step was skipped
    workflow_state = load_workflow_state()
    skipped_steps = workflow_state.get('skipped_steps', [])
    bibtex_skipped = 'Step 3: Get BibTeX' in skipped_steps
    conf_rank_not_possible = bibtex_skipped
    
    return render_template('generate_conf_rank.html',
                         default_iteration=default_iteration,
                         venue_ranks=venue_ranks,
                         workflow_info=workflow_info,
                         bibtex_skipped=bibtex_skipped,
                         conf_rank_not_possible=conf_rank_not_possible)


@app.route('/api/workflow/generate_conf_rank/unindexed_venues', methods=['GET'])
def get_unindexed_venues():
    """Get list of unindexed venues for an iteration"""
    try:
        print("Getting unindexed venues...")
        iteration = int(request.args.get('iteration'))
        
        # Get database path from config
        search_conf = load_search_conf()
        if not search_conf or 'db_path' not in search_conf:
            return jsonify({'error': 'Database path not configured'}), 400
        
        db_path = search_conf['db_path']
        db_manager = DBManager(db_path)
        
        # Get articles with BibTeX
        print("[DEBUG] iteration: ", iteration)
        articles = db_manager.get_iteration_data(
            iteration=iteration,
            bibtex__not_empty=True,
            bibtex__ne="NO_BIBTEX",
            selected=SelectionStage.NOT_SELECTED
        )
        
        # Get venues from articles
        venues = get_venues(articles)
        print("Venues: ", venues)
        
        # Get existing conf_rank data
        conf_rank_data = db_manager.get_conf_rank_data()
        conf_rank = {venue: rank for venue, rank in conf_rank_data}
        
        # Filter unindexed venues (excluding arxiv/ssrn/corr)
        unindexed_venues = []
        for venue in venues:
            venue = venue.strip().replace("\n", " ")
            venue_lower = venue.lower()
            
            # Skip if already in conf_rank (case-insensitive)
            if venue_lower not in [k.lower() for k in conf_rank.keys()]:
                # Auto-assign NA for arxiv/ssrn/corr
                if "arxiv" in venue_lower or "ssrn" in venue_lower or 'corr' in venue_lower:
                    # Auto-save these
                    db_manager.insert_conf_rank_data([(venue, "NA")])
                    continue
                unindexed_venues.append(venue)
        
        return jsonify({
            'success': True,
            'unindexed_venues': unindexed_venues,
            'total': len(unindexed_venues)
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/workflow/generate_conf_rank/similar_venues', methods=['GET'])
def get_similar_venues():
    """Get similar venues for a given venue"""
    try:
        venue = request.args.get('venue')
        if not venue:
            return jsonify({'error': 'Venue parameter required'}), 400
        
        # Get database path from config
        search_conf = load_search_conf()
        if not search_conf or 'db_path' not in search_conf:
            return jsonify({'error': 'Database path not configured'}), 400
        
        db_path = search_conf['db_path']
        db_manager = DBManager(db_path)
        
        # Get existing conf_rank data
        conf_rank_data = db_manager.get_conf_rank_data()
        conf_rank_dict = {venue_name: rank for venue_name, rank in conf_rank_data}
        
        # Find similar venues (adapting the function to accept conf_rank)
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity
        
        existing_venues = set(conf_rank_dict.keys())
        if not existing_venues:
            return jsonify({'success': True, 'similar_venues': []})
        
        vectorizer = TfidfVectorizer()
        all_venues = [venue] + list(existing_venues)
        tfidf_matrix = vectorizer.fit_transform(all_venues)
        cosine_similarities = cosine_similarity(tfidf_matrix[0:1], tfidf_matrix[1:]).flatten()
        
        similar_venues = []
        for i, sim in enumerate(cosine_similarities):
            if sim > 0.5:  # threshold
                venue_name = list(existing_venues)[i]
                similar_venues.append({
                    'venue': venue_name,
                    'similarity': round(sim, 2),
                    'rank': conf_rank_dict[venue_name]
                })
        
        similar_venues.sort(key=lambda x: x['similarity'], reverse=True)
        similar_venues = similar_venues[:5]  # top_k
        
        return jsonify({
            'success': True,
            'similar_venues': similar_venues
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/workflow/generate_conf_rank/search_scimago', methods=['GET'])
def search_scimago_venue():
    """Search Scimago database for a venue"""
    try:
        venue = request.args.get('venue')
        if not venue:
            return jsonify({'error': 'Venue parameter required'}), 400
        
        print(f"\n{'='*80}")
        print(f"SCIMAGO SEARCH for venue: {venue}")
        print(f"{'='*80}")
        
        # Use the imported function
        result = _get_scimago_rank(venue, as_string=True)
        
        if result == "":
            print("RESULT: Not Found in database")
            print(f"{'='*80}\n")
            return jsonify({
                'success': True,
                'found': False,
                'data': 'Not Found in database'
            })
        
        print(f"RESULT:\n{result}")
        print(f"{'='*80}\n")
        
        return jsonify({
            'success': True,
            'found': True,
            'data': result
        })
        
    except Exception as e:
        print(f"ERROR in Scimago search: {str(e)}")
        import traceback
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@app.route('/api/workflow/generate_conf_rank/search_core', methods=['GET'])
def search_core_venue():
    """Search Core Table database for a venue"""
    try:
        venue = request.args.get('venue')
        if not venue:
            return jsonify({'error': 'Venue parameter required'}), 400
        
        print(f"\n{'='*80}")
        print(f"CORE TABLE SEARCH for venue: {venue}")
        print(f"{'='*80}")
        
        # Use the imported function
        result = _get_core_rank(venue, as_string=True)
        
        if result == "":
            print("RESULT: Not Found in database")
            print(f"{'='*80}\n")
            return jsonify({
                'success': True,
                'found': False,
                'data': 'Not Found in database'
            })
        
        print(f"RESULT:\n{result}")
        print(f"{'='*80}\n")
        
        return jsonify({
            'success': True,
            'found': True,
            'data': result
        })
        
    except Exception as e:
        print(f"ERROR in Core Table search: {str(e)}")
        import traceback
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@app.route('/api/workflow/generate_conf_rank/save_rank', methods=['POST'])
def save_venue_rank():
    """Save a venue rank to the database"""
    try:
        data = request.get_json()
        venue = data.get('venue')
        rank = data.get('rank')
        
        if not venue or not rank:
            return jsonify({'error': 'Venue and rank are required'}), 400
        
        # Get database path from config
        search_conf = load_search_conf()
        if not search_conf or 'db_path' not in search_conf:
            return jsonify({'error': 'Database path not configured'}), 400
        
        db_path = search_conf['db_path']
        db_manager = DBManager(db_path)
        
        # Save the rank
        db_manager.insert_conf_rank_data([(venue, rank)])
        
        # Get current iteration from workflow state or config
        workflow_info = get_workflow_info()
        current_iter = workflow_info.get('current_iteration') if workflow_info else None
        if current_iter is None:
            current_iter = search_conf.get('current_iteration')
        
        # Update workflow state when saving ranks
        # Note: This updates on every venue save, but that's okay for tracking progress
        update_workflow_state(
            db_path=db_path,
            current_iteration=current_iter,
            last_step="Step 4: Assign Venue Ranks"
        )
        
        return jsonify({
            'success': True,
            'message': f'Rank "{rank}" saved for venue "{venue}"'
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/workflow/filter_by_metadata', methods=['GET'])
def filter_by_metadata_page():
    """Page for Step 5: Filter by Metadata"""
    search_conf = load_search_conf()
    
    # Get workflow info
    workflow_info = get_workflow_info()
    current_iteration = workflow_info.get('current_iteration') if workflow_info else None
    
    # Default to current iteration if available
    default_iteration = current_iteration if current_iteration is not None else 0
    
    # Get config values for display
    start_year = search_conf.get('start_year', 2019) if search_conf else 2019
    end_year = search_conf.get('end_year', 2025) if search_conf else 2025
    venue_rank_list = search_conf.get('venue_rank_list', []) if search_conf else []
    
    # Check if BibTeX or Generate Conf Rank steps were skipped
    workflow_state = load_workflow_state()
    skipped_steps = workflow_state.get('skipped_steps', [])
    bibtex_skipped = 'Step 3: Get BibTeX' in skipped_steps
    conf_rank_skipped = 'Step 4: Assign Venue Ranks' in skipped_steps
    venue_filter_disabled = bibtex_skipped or conf_rank_skipped
    default_reject_preprints = search_conf.get('reject_preprints', True) if search_conf else True

    return render_template('filter_by_metadata.html',
                         default_iteration=default_iteration,
                         start_year=start_year,
                         end_year=end_year,
                         venue_rank_list=venue_rank_list,
                         workflow_info=workflow_info,
                         venue_filter_disabled=venue_filter_disabled,
                         bibtex_skipped=bibtex_skipped,
                         conf_rank_skipped=conf_rank_skipped,
                         default_reject_preprints=default_reject_preprints)


@app.route('/api/workflow/filter_by_metadata/articles', methods=['GET'])
def get_articles_for_metadata_filter():
    """Get list of articles that need metadata filtering for an iteration"""
    try:
        iteration = int(request.args.get('iteration'))
        
        # Get database path from config
        search_conf = load_search_conf()
        if not search_conf or 'db_path' not in search_conf:
            return jsonify({'error': 'Database path not configured'}), 400
        
        db_path = search_conf['db_path']
        db_manager = DBManager(db_path)

        print("Iteration: ", iteration)
        
        # Check if BibTeX or Generate Conf Rank steps were skipped
        workflow_state = load_workflow_state()
        skipped_steps = workflow_state.get('skipped_steps', [])
        bibtex_skipped = 'Step 3: Get BibTeX' in skipped_steps
        conf_rank_skipped = 'Step 4: Assign Venue Ranks' in skipped_steps
        venue_filter_disabled = bibtex_skipped or conf_rank_skipped
        
        # If venue filtering is disabled (BibTeX or Conf Rank skipped), 
        # get articles without requiring BibTeX
        if venue_filter_disabled:
            # Get all articles that haven't been selected yet (don't require BibTeX)
            articles = db_manager.get_iteration_data(
                iteration=iteration,
                selected=SelectionStage.NOT_SELECTED
            )
        else:
            # Get articles with BibTeX that haven't been selected yet
            articles = db_manager.get_iteration_data(
                iteration=iteration,
                bibtex__not_empty=True,
                bibtex__ne="NO_BIBTEX",
                selected=SelectionStage.NOT_SELECTED
            )

        print("Articles: ", len(articles))
        
        # Convert to JSON-serializable format
        articles_data = []
        for article in articles:
            articles_data.append({
                'id': article.id,
                'title': article.title or '',
                'venue': article.venue or '',
                'pub_year': article.pub_year or '',
                'eprint_url': article.eprint_url or '',
                'pub_url': article.pub_url or '',
                'bibtex': article.bibtex or ''
            })
        
        return jsonify({
            'success': True,
            'articles': articles_data,
            'total': len(articles_data),
            'venue_filter_disabled': venue_filter_disabled
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/workflow/filter_by_metadata/check_preprint', methods=['POST'])
def check_preprint_publication():
    """Check if article is a non-peer-reviewed preprint (arXiv, CORR, SSRN, etc.)"""
    try:
        data = request.get_json()
        is_preprint, reason = is_preprint_publication(
            venue=data.get('venue', ''),
            bibtex=data.get('bibtex', ''),
            eprint_url=data.get('eprint_url', ''),
            pub_url=data.get('pub_url', ''),
        )
        return jsonify({
            'success': True,
            'is_preprint': is_preprint,
            'reason': reason,
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/workflow/filter_by_metadata/check_venue', methods=['POST'])
def check_venue_peer_reviewed():
    """Check if venue is peer-reviewed and in allowed ranks"""
    try:
        data = request.get_json()
        article_id = data.get('article_id')
        bibtex = data.get('bibtex')
        
        if not article_id or not bibtex:
            return jsonify({'error': 'Article ID and BibTeX are required'}), 400
        
        # Get database path from config
        search_conf = load_search_conf()
        if not search_conf or 'db_path' not in search_conf:
            return jsonify({'error': 'Database path not configured'}), 400
        
        db_path = search_conf['db_path']
        db_manager = DBManager(db_path)
        
        # Run automated check
        result = automated_check_venue_and_peer_reviewed(bibtex, db_manager)
        
        return jsonify({
            'success': True,
            'automated_result': result,  # True, False, or None (needs user input)
            'needs_user_input': result is None
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/workflow/filter_by_metadata/check_year', methods=['POST'])
def check_year_valid():
    """Check if year is valid (web-friendly version without input prompts)"""
    try:
        data = request.get_json()
        pub_year = data.get('pub_year', '')
        
        search_conf = load_search_conf()
        if not search_conf:
            return jsonify({'error': 'Configuration not found'}), 400
        
        # Try to parse year
        try:
            year_int = int(pub_year) if pub_year and pub_year.isdigit() else 0
        except (ValueError, AttributeError):
            year_int = 0
        
        start_year = int(search_conf.get('start_year', 2019))
        end_year = int(search_conf.get('end_year', 2025))
        
        if year_int != 0:
            is_valid = start_year <= year_int <= end_year
            needs_user_input = False
        else:
            # Year is 0 or invalid - needs user confirmation
            is_valid = None
            needs_user_input = True
        
        return jsonify({
            'success': True,
            'is_valid': is_valid,
            'needs_user_input': needs_user_input,
            'year': year_int if year_int != 0 else None
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/workflow/filter_by_metadata/check_english', methods=['POST'])
def check_english_language():
    """Check if title is in English (web-friendly version)"""
    try:
        data = request.get_json()
        title = data.get('title', '')
        
        if not title:
            return jsonify({
                'success': True,
                'is_english': None,
                'needs_user_input': True
            })
        
        # Try language detection
        try:
            from langdetect import detect
            detected_lang = detect(title)
            is_english = (detected_lang == "en")
            # Always ask user to verify if not detected as English (even if detected as another language)
            # This allows user to override incorrect detections
            if is_english:
                needs_user_input = False  # Confidently English - no verification needed
            else:
                needs_user_input = True  # Not English or uncertain - user should verify
                is_english = None  # Set to None to indicate needs user verification
        except Exception:
            # If detection fails, ask user
            is_english = None
            detected_lang = None
            needs_user_input = True
        
        return jsonify({
            'success': True,
            'is_english': is_english,  # True if English, None if needs verification
            'needs_user_input': needs_user_input,
            'detected_language': detected_lang if 'detected_lang' in locals() else None
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/workflow/filter_by_metadata/check_download', methods=['POST'])
def check_downloadable():
    """Check if article is downloadable"""
    try:
        data = request.get_json()
        eprint_url = data.get('eprint_url', '')
        
        # If URL exists and is not empty, it's downloadable
        is_downloadable = bool(eprint_url and eprint_url.strip())
        
        return jsonify({
            'success': True,
            'is_downloadable': is_downloadable,
            'needs_user_input': not is_downloadable  # If no URL, ask user
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/workflow/filter_by_metadata/evaluate_article', methods=['POST'])
def evaluate_metadata_article():
    """Evaluate all checks for an article and determine if it needs user input or can be auto-processed"""
    try:
        data = request.get_json()
        article = data.get('article')
        iteration = int(data.get('iteration'))
        filter_options = data.get('filter_options', {})
        
        if not article:
            return jsonify({'error': 'Article data is required'}), 400
        
        # Skip articles without IDs - they cannot be processed
        article_id = article.get('id') or article.get('article_id')
        if not article_id:
            return jsonify({
                'success': True,
                'skip': True,
                'message': 'Article skipped: no ID available'
            })
        
        # Get database path from config
        search_conf = load_search_conf()
        if not search_conf or 'db_path' not in search_conf:
            return jsonify({'error': 'Database path not configured'}), 400
        
        db_path = search_conf['db_path']
        db_manager = DBManager(db_path)
        
        venue_filter_disabled = filter_options.get('venue_filter_disabled', False)
        enable_preprint = filter_options.get('enablePreprintCheck', True)
        enable_venue = filter_options.get('enableVenueCheck', True) and not venue_filter_disabled
        enable_year = filter_options.get('enableYearCheck', True)
        enable_language = filter_options.get('enableLanguageCheck', True)
        enable_download = filter_options.get('enableDownloadCheck', True)
        
        check_results = {}
        needs_user_input = False
        filter_result = None
        rejection_reason = None

        if enable_preprint:
            is_preprint, reason = is_preprint_publication(
                venue=article.get('venue', ''),
                bibtex=article.get('bibtex', ''),
                eprint_url=article.get('eprint_url', ''),
                pub_url=article.get('pub_url', ''),
            )
            check_results['preprint'] = is_preprint
            if is_preprint:
                filter_result = 'venue_filtered'
                rejection_reason = reason

        if enable_venue and filter_result is None:
            bibtex = article.get('bibtex', '')
            if bibtex:
                result = automated_check_venue_and_peer_reviewed(bibtex, db_manager)
                check_results['venue'] = result
                if result is False:
                    # Venue check failed - immediately filter and skip other checks
                    filter_result = 'venue_filtered'
                elif result is None:
                    # Venue check needs user input, but continue checking other filters
                    needs_user_input = True
            else:
                check_results['venue'] = None
                needs_user_input = True
        
        # Check year (only if venue didn't fail)
        if enable_year and filter_result is None:
            pub_year = article.get('pub_year', '')
            try:
                year_int = int(pub_year) if pub_year and pub_year.isdigit() else 0
            except (ValueError, AttributeError):
                year_int = 0
            
            start_year = int(search_conf.get('start_year', 2019))
            end_year = int(search_conf.get('end_year', 2025))
            
            if year_int != 0:
                year_check = start_year <= year_int <= end_year
                check_results['year'] = year_check
                if year_check is False:
                    # Year check failed - immediately filter and skip remaining checks
                    filter_result = 'year_filtered'
            else:
                check_results['year'] = None
                # Year needs user input, but continue checking other filters
                needs_user_input = True
        
        # Check language (only if previous checks didn't fail)
        if enable_language and filter_result is None:
            title = article.get('title', '')
            if title:
                try:
                    from langdetect import detect
                    detected_lang = detect(title)
                    is_english = (detected_lang == "en")
                    if is_english:
                        # Confidently detected as English - no user input needed
                        check_results['language'] = True
                    else:
                        # Detected as non-English OR detection uncertain - needs user verification
                        check_results['language'] = None
                        needs_user_input = True
                except Exception:
                    # Detection failed - needs user input
                    check_results['language'] = None
                    needs_user_input = True
            else:
                # No title - needs user input
                check_results['language'] = None
                needs_user_input = True
        
        # Check download (only if previous checks didn't fail)
        if enable_download and filter_result is None:
            eprint_url = article.get('eprint_url', '')
            download_check = bool(eprint_url and eprint_url.strip())
            check_results['download'] = download_check
            if download_check is False:
                # Download check failed - immediately filter
                filter_result = 'download_filtered'
        
        # If no filter failed yet, determine final result
        if filter_result is None:
            if not needs_user_input:
                # All checks have definitive results and none failed
                filter_result = 'approved'
            # else: needs_user_input is True, filter_result remains None
        
        return jsonify({
            'success': True,
            'check_results': check_results,
            'needs_user_input': needs_user_input and filter_result is None,
            'filter_result': filter_result,
            'rejection_reason': rejection_reason,
        })
        
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@app.route('/api/workflow/filter_by_metadata/save_result', methods=['POST'])
def save_metadata_filter_result():
    """Save the metadata filter result for an article"""
    try:
        data = request.get_json()
        article_id = data.get('article_id')
        filter_result = data.get('filter_result')  # 'approved', 'venue_filtered', 'year_filtered', 'language_filtered', 'download_filtered'
        iteration = int(data.get('iteration'))
        
        # Skip articles without IDs - return success but don't process
        if not article_id:
            return jsonify({
                'success': True,
                'skipped': True,
                'message': 'Article skipped: no ID available'
            })
        
        if not filter_result:
            return jsonify({'error': 'Filter result is required'}), 400
        
        # Get database path from config
        search_conf = load_search_conf()
        if not search_conf or 'db_path' not in search_conf:
            return jsonify({'error': 'Database path not configured'}), 400
        
        db_path = search_conf['db_path']
        db_manager = DBManager(db_path)
        
        # Prepare update data based on filter result
        update_data = []
        if filter_result == 'approved':
            update_data.append((article_id, SelectionStage.METADATA_APPROVED.value, "selected"))
        elif filter_result == 'venue_filtered':
            update_data.append((article_id, True, "venue_filtered_out"))
        elif filter_result == 'year_filtered':
            update_data.append((article_id, True, "year_filtered_out"))
        elif filter_result == 'language_filtered':
            update_data.append((article_id, True, "language_filtered_out"))
        elif filter_result == 'download_filtered':
            update_data.append((article_id, True, "download_filtered_out"))
        else:
            return jsonify({'error': 'Invalid filter result'}), 400
        
        # Save to database
        db_manager.update_batch_iteration_data(iteration, update_data)
        
        # Update workflow state
        update_workflow_state(
            db_path=db_path,
            current_iteration=iteration,
            last_step="Step 5: Filter by Metadata"
        )
        
        return jsonify({
            'success': True,
            'message': f'Filter result saved for article {article_id}'
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/workflow/filter_by_title', methods=['GET'])
def filter_by_title_page():
    """Page for Step 6: Filter by Title"""
    search_conf = load_search_conf()
    
    # Get workflow info
    workflow_info = get_workflow_info()
    current_iteration = workflow_info.get('current_iteration') if workflow_info else None
    
    # Default to current iteration if available
    default_iteration = current_iteration if current_iteration is not None else 0
    
    # Get default rater from config
    default_rater = search_conf.get('rater', 'default') if search_conf else 'default'
    
    # Get default topic from config
    default_topic = search_conf.get('topic', '') if search_conf else ''
    
    return render_template('filter_by_title.html',
                         default_iteration=default_iteration,
                         default_rater=default_rater,
                         default_topic=default_topic,
                         workflow_info=workflow_info)


@app.route('/api/workflow/filter_by_title/articles', methods=['GET'])
def get_articles_for_title_filter():
    """Get list of articles that need title filtering. Includes already-screened (for editing); sorted with screened first; start_index for first unevaluated."""
    try:
        iteration = int(request.args.get('iteration'))
        rater = (request.args.get('rater') or '').strip()
        search_conf = load_search_conf()
        if not search_conf or 'db_path' not in search_conf:
            return jsonify({'error': 'Database path not configured'}), 400
        db_path = search_conf['db_path']
        db_manager = DBManager(db_path)

        workflow_state = load_workflow_state()
        skipped_steps = workflow_state.get('skipped_steps', [])
        metadata_skipped = 'Step 5: Filter by Metadata' in skipped_steps

        # Include METADATA_APPROVED (1) and TITLE_APPROVED (2) so we show all: not evaluated, rejected, and approved
        if metadata_skipped:
            articles_metadata = db_manager.get_iteration_data(
                iteration=iteration,
                selected__in=[SelectionStage.METADATA_APPROVED.value, SelectionStage.TITLE_APPROVED.value]
            )
            articles_not_selected = db_manager.get_iteration_data(
                iteration=iteration, selected=SelectionStage.NOT_SELECTED
            )
            article_dict = {}
            for article in articles_metadata:
                article_dict[article.id] = article
            for article in articles_not_selected:
                if article.id not in article_dict:
                    article_dict[article.id] = article
            articles = list(article_dict.values())
        else:
            articles = db_manager.get_iteration_data(
                iteration=iteration,
                selected__in=[SelectionStage.METADATA_APPROVED.value, SelectionStage.TITLE_APPROVED.value]
            )
            if len(articles) == 0:
                articles = db_manager.get_iteration_data(
                    iteration=iteration, selected=SelectionStage.NOT_SELECTED
                )

        # Return all articles in scope (approved, rejected, not yet evaluated) so the UI can show and navigate the full list
        # Deduplicate by id
        seen = set()
        articles = [a for a in articles if a.id not in seen and not seen.add(a.id)]

        article_ids = [a.id for a in articles]
        existing_screening = []
        if rater and article_ids:
            try:
                existing_screening = db_manager.get_screening_data_for_rater(article_ids, iteration, rater, phase="title")
            except Exception:
                pass
        by_id = {row['id']: row for row in existing_screening}

        # Sort: previously screened first, unscreened last
        articles.sort(key=lambda a: (0 if a.id in by_id else 1))
        start_index = 0
        while start_index < len(articles) and articles[start_index].id in by_id:
            start_index += 1
        # If all assigned, start at last article
        if start_index >= len(articles) and articles:
            start_index = len(articles) - 1

        articles_data = []
        for article in articles:
            row = by_id.get(article.id)
            keep = None
            reason = ''
            if row is not None:
                kv = row.get('keep_title')
                keep = True if kv in (1, True, '1') else False if kv is not None else None
                reason = (row.get('reason_title') or '') or ''
            articles_data.append({
                'id': article.id,
                'title': article.title or '',
                'venue': article.venue or '',
                'pub_year': article.pub_year or '',
                'eprint_url': article.eprint_url or '',
                'authors': article.authors or '',
                'title_reason': reason,
                'previous_decision': 'approve' if keep else 'reject' if keep is False else None,
            })
        return jsonify({
            'success': True,
            'articles': articles_data,
            'total': len(articles_data),
            'start_index': start_index,
        })
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@app.route('/api/workflow/filter_by_title/save_result', methods=['POST'])
def save_title_filter_result():
    """Save the title filter result for an article"""
    try:
        data = request.get_json()
        article_id = data.get('article_id')
        decision = data.get('decision')  # 'approve' or 'reject'
        reason = data.get('reason', '')  # Optional reason
        iteration = int(data.get('iteration'))
        
        if not article_id or not decision:
            return jsonify({'error': 'Article ID and decision are required'}), 400
        
        if decision not in ['approve', 'reject']:
            return jsonify({'error': 'Decision must be "approve" or "reject"'}), 400
        
        # Get database path from config
        search_conf = load_search_conf()
        if not search_conf or 'db_path' not in search_conf:
            return jsonify({'error': 'Database path not configured'}), 400
        
        db_path = search_conf['db_path']
        db_manager = DBManager(db_path)
        
        # Get rater from request (required)
        rater = data.get('rater')
        if not rater:
            return jsonify({'error': 'Rater ID is required'}), 400
        
        # Ensure screening table exists (with annotations if configured)
        annotations = search_conf.get('annotations', [])
        db_manager.create_screening_table(annotations)
        
        # Get the article object to pass to apply_decision
        articles = db_manager.get_iteration_data(iteration=iteration, id=article_id)
        if not articles:
            return jsonify({'error': f'Article {article_id} not found'}), 404
        
        article = articles[0]
        
        # Convert 'approve'/'reject' to 'y'/'n' format expected by apply_decision
        decision_char = 'y' if decision == 'approve' else 'n'
        
        # Use apply_decision from screening.py to insert into screening table only.
        # Iterations table keep_title/selected are updated only in solve_title_disagreements.
        apply_decision(
            db_manager=db_manager,
            article=article,
            iteration=iteration,
            rater=rater,
            decision=decision_char,
            reason=reason or '',
            screening_phase="title"
        )
        
        # Update workflow state
        update_workflow_state(
            db_path=db_path,
            current_iteration=iteration,
            last_step="Step 6: Filter by Title"
        )
        
        return jsonify({
            'success': True,
            'message': f'Title filter result saved for article {article_id}'
        })
        
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@app.route('/api/workflow/filter_by_title/prompts', methods=['GET'])
def get_title_filter_prompts():
    """Get default prompts for title filtering"""
    try:
        # Get prompt file paths
        prompts_folder = os.path.join("utils", "prompts")
        system_prompt_file = os.path.join(prompts_folder, "system_title_screening.txt")
        user_prompt_file = os.path.join(prompts_folder, "user_title_screening.txt")
        
        # If the regular user prompt file doesn't exist, try the copy
        if not os.path.exists(user_prompt_file):
            user_prompt_file = os.path.join(prompts_folder, "user_title_screening copy.txt")
        
        system_prompt = ""
        user_prompt = ""
        
        if os.path.exists(system_prompt_file):
            with open(system_prompt_file, 'r', encoding='utf-8') as f:
                system_prompt = f.read()
        
        if os.path.exists(user_prompt_file):
            with open(user_prompt_file, 'r', encoding='utf-8') as f:
                user_prompt = f.read()
        
        return jsonify({
            'success': True,
            'system_prompt': system_prompt,
            'user_prompt': user_prompt
        })
        
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@app.route('/api/workflow/filter_by_title/run_llm_filtering', methods=['POST'])
def run_title_llm_filtering():
    """Execute LLM filtering for title screening"""
    try:
        import tempfile
        # Support both JSON (for backwards compatibility) and form-data (for file uploads)
        if request.is_json:
            data = request.get_json()
            api_key_file = data.get('api_key_file')  # Path string
        else:
            # Form data with file upload
            data = request.form.to_dict()
            api_key_file = None
            
            # Handle API key file upload
            if 'api_key_file' in request.files:
                uploaded_file = request.files['api_key_file']
                if uploaded_file and uploaded_file.filename:
                    # Save uploaded file to uploads folder
                    filename = secure_filename(uploaded_file.filename)
                    # Ensure it's a .txt file
                    if not filename.endswith('.txt'):
                        filename += '.txt'
                    upload_path = os.path.join(UPLOAD_FOLDER, f"api_key_{filename}")
                    uploaded_file.save(upload_path)
                    api_key_file = upload_path
            elif 'api_key_file_path' in data:
                # User provided a path manually (server-side path)
                api_key_file = data.get('api_key_file_path').strip() if data.get('api_key_file_path') else None
        
        iteration = int(data.get('iteration'))
        rater = data.get('rater')
        model = data.get('model', 'gpt-4o')
        topic = data.get('topic')
        system_prompt = data.get('system_prompt')
        user_prompt = data.get('user_prompt')
        
        if not rater:
            return jsonify({'error': 'Rater ID is required'}), 400
        
        if not topic:
            return jsonify({'error': 'Topic is required'}), 400
        
        if not system_prompt or not user_prompt:
            return jsonify({'error': 'Both system and user prompts are required'}), 400
        
        # Get database path from config
        search_conf = load_search_conf()
        if not search_conf or 'db_path' not in search_conf:
            return jsonify({'error': 'Database path not configured'}), 400
        
        db_path = search_conf['db_path']
        
        # Ensure screening table exists
        db_manager = DBManager(db_path)
        annotations_config = search_conf.get('annotations', [])
        db_manager.create_screening_table(annotations_config)
        
        # Check if user wants to fill annotations
        fill_annotations = data.get('fill_annotations', 'true').lower() == 'true' if isinstance(data.get('fill_annotations'), str) else data.get('fill_annotations', True)
        
        # Create temporary prompt files
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as sys_file:
            sys_file.write(system_prompt)
            system_prompt_file = sys_file.name
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as user_file:
            user_file.write(user_prompt)
            user_prompt_file = user_file.name
        
        try:
            # Convert annotations dict to the format expected by screen_papers
            # Only include annotations if user wants them filled
            annotations_dict = {}
            if fill_annotations and annotations_config:
                # annotations_config is a list, convert to dict if needed
                if isinstance(annotations_config, list):
                    annotations_dict = {ann: ann for ann in annotations_config}  # Use annotation name as description
                elif isinstance(annotations_config, dict):
                    annotations_dict = annotations_config
            
            # Execute LLM screening
            results = screen_papers(
                rater_id=rater,
                topic=topic,
                db_path=db_path,
                iteration=iteration,
                stage="title",
                model=model,
                api_key=api_key_file,
                annotations=annotations_dict,
                system_prompt_file=system_prompt_file,
                user_prompt_file=user_prompt_file
            )
            
            # Update workflow state
            update_workflow_state(
                db_path=db_path,
                current_iteration=iteration,
                last_step="Step 6: Filter by Title"
            )
            
            return jsonify({
                'success': True,
                'processed_count': len(results),
                'message': f'LLM filtering completed. Processed {len(results)} article(s).'
            })
            
        finally:
            # Clean up temporary files
            try:
                if os.path.exists(system_prompt_file):
                    os.unlink(system_prompt_file)
                if os.path.exists(user_prompt_file):
                    os.unlink(user_prompt_file)
            except:
                pass
        
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@app.route('/workflow/filter_by_content', methods=['GET'])
def filter_by_content_page():
    """Page for Step 8: Filter by Content"""
    search_conf = load_search_conf()
    
    # Get workflow info
    workflow_info = get_workflow_info()
    current_iteration = workflow_info.get('current_iteration') if workflow_info else None
    
    # Default to current iteration if available
    default_iteration = current_iteration if current_iteration is not None else 0
    
    # Get rater from config
    default_rater = search_conf.get('rater', 'default') if search_conf else 'default'
    
    # Get annotations from config
    annotations = search_conf.get('annotations', []) if search_conf else []
    
    # Get default topic from config
    default_topic = search_conf.get('topic', '') if search_conf else ''
    
    return render_template('filter_by_content.html',
                         default_iteration=default_iteration,
                         default_rater=default_rater,
                         default_topic=default_topic,
                         workflow_info=workflow_info,
                         annotations=annotations)


@app.route('/api/workflow/filter_by_content/articles', methods=['GET'])
def get_articles_for_content_filter():
    """Get articles that need content filtering. Includes already-screened (for editing); sorted with screened first; start_index for first unevaluated."""
    try:
        iteration = int(request.args.get('iteration', 0))
        rater = (request.args.get('rater') or '').strip()
        search_conf = load_search_conf()
        if not search_conf or 'db_path' not in search_conf:
            return jsonify({'error': 'Database path not configured'}), 400
        db_path = search_conf['db_path']
        db_manager = DBManager(db_path)

        # Include TITLE_APPROVED (2) and CONTENT_APPROVED (3) so we show all: not evaluated, rejected, and approved
        articles = db_manager.get_iteration_data(
            iteration=iteration,
            selected__in=[SelectionStage.TITLE_APPROVED.value, SelectionStage.CONTENT_APPROVED.value]
        )
        filtered_articles = list(articles)
        seen = set()
        filtered_articles = [a for a in filtered_articles if a.id not in seen and not seen.add(a.id)]
        article_ids = [a.id for a in filtered_articles]
        existing_screening = []
        if rater and article_ids:
            try:
                existing_screening = db_manager.get_screening_data_for_rater(article_ids, iteration, rater, phase="content")
            except Exception:
                pass
        by_id = {row['id']: row for row in existing_screening}
        filtered_articles.sort(key=lambda a: (0 if a.id in by_id else 1))
        start_index = 0
        while start_index < len(filtered_articles) and filtered_articles[start_index].id in by_id:
            start_index += 1
        # If all assigned, start at last article
        if start_index >= len(filtered_articles) and filtered_articles:
            start_index = len(filtered_articles) - 1

        annotations_config = search_conf.get('annotations', [])
        articles_data = []
        for article in filtered_articles:
            row = by_id.get(article.id)
            reason_text = ''
            annotations_data = {}
            previous_decision = None
            if row is not None:
                kv = row.get('keep_content')
                previous_decision = 'approve' if kv in (1, True, '1') else 'reject' if kv is not None else None
                reason_text = (row.get('reason_content') or '') or ''
                for ann in annotations_config:
                    v = row.get(ann)
                    if v is not None and str(v).strip():
                        annotations_data[ann] = str(v).strip()
            eprint_url = getattr(article, 'eprint_url', '') or ''
            pub_url = getattr(article, 'pub_url', '') or ''
            articles_data.append({
                'id': article.id,
                'title': getattr(article, 'title', '') or '',
                'venue': getattr(article, 'venue', '') or '',
                'pub_year': getattr(article, 'pub_year', '') or '',
                'eprint_url': eprint_url,
                'pub_url': pub_url,
                'authors': getattr(article, 'authors', '') or '',
                'content_reason': reason_text,
                'annotations': annotations_data,
                'previous_decision': previous_decision,
            })
        return jsonify({
            'success': True,
            'articles': articles_data,
            'total': len(articles_data),
            'start_index': start_index,
        })
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@app.route('/api/workflow/filter_by_content/save_result', methods=['POST'])
def save_content_filter_result():
    """Save the content filter result for an article"""
    try:
        data = request.get_json()
        article_id = data.get('article_id')
        decision = data.get('decision')  # 'approve' or 'reject'
        reason = data.get('reason', '')  # Optional reason
        annotations = data.get('annotations', {})  # Optional annotations dict
        iteration = int(data.get('iteration'))
        
        if not article_id or not decision:
            return jsonify({'error': 'Article ID and decision are required'}), 400
        
        if decision not in ['approve', 'reject']:
            return jsonify({'error': 'Decision must be "approve" or "reject"'}), 400
        
        # Get database path from config
        search_conf = load_search_conf()
        if not search_conf or 'db_path' not in search_conf:
            return jsonify({'error': 'Database path not configured'}), 400
        
        db_path = search_conf['db_path']
        db_manager = DBManager(db_path)
        
        # Get rater from request (required)
        rater = data.get('rater')
        if not rater:
            return jsonify({'error': 'Rater ID is required'}), 400
        
        # Ensure screening table exists (with annotations if configured)
        annotations_config = search_conf.get('annotations', [])
        db_manager.create_screening_table(annotations_config)
        
        # Get the article object to pass to apply_decision
        articles = db_manager.get_iteration_data(iteration=iteration, id=article_id)
        if not articles:
            return jsonify({'error': f'Article {article_id} not found'}), 404
        
        article = articles[0]
        
        # Convert 'approve'/'reject' to 'y'/'n' format expected by apply_decision
        decision_char = 'y' if decision == 'approve' else 'n'
        
        # Use apply_decision from screening.py to insert into screening table
        # Pass annotations as keyword arguments (only if approving)
        if decision == 'approve' and annotations:
            apply_decision(
                db_manager=db_manager,
                article=article,
                iteration=iteration,
                rater=rater,
                decision=decision_char,
                reason=reason or '',
                screening_phase="content",
                **annotations
            )
        else:
            apply_decision(
                db_manager=db_manager,
                article=article,
                iteration=iteration,
                rater=rater,
                decision=decision_char,
                reason=reason or '',
                screening_phase="content"
            )
        
        # Iterations table keep_content/selected are updated only in solve_content_disagreements.
        
        # Update workflow state
        update_workflow_state(
            db_path=db_path,
            current_iteration=iteration,
            last_step="Step 8: Filter by Content"
        )
        
        return jsonify({
            'success': True,
            'message': f'Content filter result saved for article {article_id}'
        })
        
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@app.route('/api/workflow/filter_by_content/prompts', methods=['GET'])
def get_content_filter_prompts():
    """Get default prompts for content filtering"""
    try:
        # Get prompt file paths
        prompts_folder = os.path.join("utils", "prompts")
        system_prompt_file = os.path.join(prompts_folder, "system_content_screening.txt")
        user_prompt_file = os.path.join(prompts_folder, "user_content_screening.txt")
        
        # If the regular system prompt file doesn't exist, try the copy
        if not os.path.exists(system_prompt_file):
            system_prompt_file = os.path.join(prompts_folder, "system_content_screening copy.txt")
        
        system_prompt = ""
        user_prompt = ""
        
        if os.path.exists(system_prompt_file):
            with open(system_prompt_file, 'r', encoding='utf-8') as f:
                system_prompt = f.read()
        
        if os.path.exists(user_prompt_file):
            with open(user_prompt_file, 'r', encoding='utf-8') as f:
                user_prompt = f.read()
        
        return jsonify({
            'success': True,
            'system_prompt': system_prompt,
            'user_prompt': user_prompt
        })
        
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@app.route('/api/workflow/filter_by_content/download_pdfs', methods=['POST'])
def download_content_pdfs():
    """Download PDFs for content filtering"""
    try:
        data = request.get_json()
        iteration = int(data.get('iteration'))
        pdf_folder = data.get('pdf_folder')
        
        if not pdf_folder:
            return jsonify({'error': 'PDF folder path is required'}), 400
        
        # Get database path from config
        search_conf = load_search_conf()
        if not search_conf or 'db_path' not in search_conf:
            return jsonify({'error': 'Database path not configured'}), 400
        
        db_path = search_conf['db_path']
        
        # Get articles that need content filtering (title-approved)
        articles = get_articles_from_db(db_path, iteration, stage="content")
        
        if not articles:
            return jsonify({
                'success': True,
                'downloaded_count': 0,
                'failed_count': 0,
                'message': 'No articles found for content filtering'
            })
        
        # Create folder if it doesn't exist
        os.makedirs(pdf_folder, exist_ok=True)
        
        # Download PDFs (skip manual prompt for webapp, return failed downloads)
        failed_downloads = download_pdfs(articles, pdf_folder, skip_manual_prompt=True)
        
        # Count downloaded and failed PDFs
        downloaded_count = 0
        failed_count = 0
        failed_details = []
        for article in articles:
            pdf_path = os.path.join(pdf_folder, f"{article.id}.pdf")
            if os.path.exists(pdf_path) and is_valid_pdf(pdf_path):
                downloaded_count += 1
            else:
                failed_count += 1
                # Check if this article was in failed downloads
                if failed_downloads and any(failed.id == article.id for failed in failed_downloads):
                    url = article.eprint_url or article.pub_url or 'No URL available'
                    failed_details.append({
                        'article_id': article.id,
                        'title': article.title or 'No title',
                        'url': url,
                        'expected_filename': f"{article.id}.pdf"
                    })
        
        response_data = {
            'success': True,
            'downloaded_count': downloaded_count,
            'failed_count': failed_count,
            'message': f'PDF download completed. Downloaded {downloaded_count} PDF(s).'
        }
        
        if failed_details:
            response_data['failed_downloads'] = failed_details
            response_data['message'] += f' {failed_count} PDF(s) need manual download.'
        
        return jsonify(response_data)
        
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@app.route('/api/workflow/filter_by_content/verify_pdf', methods=['POST'])
def verify_pdf_download():
    """Verify if a manually downloaded PDF exists and is valid"""
    try:
        data = request.get_json()
        article_id = data.get('article_id')
        pdf_folder = data.get('pdf_folder')
        
        if not article_id or not pdf_folder:
            return jsonify({'error': 'Article ID and PDF folder are required'}), 400
        
        pdf_path = os.path.join(pdf_folder, f"{article_id}.pdf")
        
        if not os.path.exists(pdf_path):
            return jsonify({
                'success': True,
                'exists': False,
                'valid': False,
                'message': f'File {article_id}.pdf not found'
            })
        
        # Check if it's a valid PDF
        valid = is_valid_pdf(pdf_path)
        
        return jsonify({
            'success': True,
            'exists': True,
            'valid': valid,
            'message': f'File {article_id}.pdf exists and is {"valid" if valid else "invalid"}'
        })
        
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@app.route('/api/workflow/filter_by_content/run_llm_filtering', methods=['POST'])
def run_content_llm_filtering():
    """Execute LLM filtering for content screening"""
    try:
        import tempfile
        # Support both JSON (for backwards compatibility) and form-data (for file uploads)
        api_key_file = None
        
        if request.is_json:
            data = request.get_json()
            api_key_file = data.get('api_key_file')  # Path string (can be None)
        else:
            # Form data with file upload
            data = request.form.to_dict()
            
            # Handle API key file upload
            if 'api_key_file' in request.files:
                uploaded_file = request.files['api_key_file']
                if uploaded_file and uploaded_file.filename:
                    # Save uploaded file to uploads folder
                    filename = secure_filename(uploaded_file.filename)
                    # Ensure it's a .txt file
                    if not filename.endswith('.txt'):
                        filename += '.txt'
                    upload_path = os.path.join(UPLOAD_FOLDER, f"api_key_{filename}")
                    uploaded_file.save(upload_path)
                    api_key_file = upload_path
            elif 'api_key_file_path' in data:
                # User provided a path manually
                api_key_file = data.get('api_key_file_path').strip() if data.get('api_key_file_path') else None
        
        iteration = int(data.get('iteration'))
        rater = data.get('rater')
        model = data.get('model', 'gpt-4o')
        pdf_folder = data.get('pdf_folder')
        topic = data.get('topic')
        system_prompt = data.get('system_prompt')
        user_prompt = data.get('user_prompt')
        
        if not rater:
            return jsonify({'error': 'Rater ID is required'}), 400
        
        if not pdf_folder:
            return jsonify({'error': 'PDF folder path is required'}), 400
        
        if not topic:
            return jsonify({'error': 'Topic is required'}), 400
        
        if not system_prompt or not user_prompt:
            return jsonify({'error': 'Both system and user prompts are required'}), 400
        
        # Get database path from config
        search_conf = load_search_conf()
        if not search_conf or 'db_path' not in search_conf:
            return jsonify({'error': 'Database path not configured'}), 400
        
        db_path = search_conf['db_path']
        
        # Ensure screening table exists
        db_manager = DBManager(db_path)
        annotations_config = search_conf.get('annotations', [])
        db_manager.create_screening_table(annotations_config)
        
        # Check if user wants to fill annotations
        fill_annotations = data.get('fill_annotations', 'true').lower() == 'true' if isinstance(data.get('fill_annotations'), str) else data.get('fill_annotations', True)
        
        # Create temporary prompt files
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as sys_file:
            sys_file.write(system_prompt)
            system_prompt_file = sys_file.name
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as user_file:
            user_file.write(user_prompt)
            user_prompt_file = user_file.name
        
        try:
            # Convert annotations dict to the format expected by screen_papers
            # Only include annotations if user wants them filled
            annotations_dict = {}
            if fill_annotations and annotations_config:
                # annotations_config is a list, convert to dict if needed
                if isinstance(annotations_config, list):
                    annotations_dict = {ann: ann for ann in annotations_config}  # Use annotation name as description
                elif isinstance(annotations_config, dict):
                    annotations_dict = annotations_config
            
            # Execute LLM screening
            results = screen_papers(
                rater_id=rater,
                topic=topic,
                db_path=db_path,
                iteration=iteration,
                stage="content",
                model=model,
                api_key=api_key_file,
                annotations=annotations_dict,
                article_folder=pdf_folder,
                system_prompt_file=system_prompt_file,
                user_prompt_file=user_prompt_file
            )
            
            # Update workflow state
            update_workflow_state(
                db_path=db_path,
                current_iteration=iteration,
                last_step="Step 8: Filter by Content"
            )
            
            return jsonify({
                'success': True,
                'processed_count': len(results),
                'message': f'LLM filtering completed. Processed {len(results)} article(s).'
            })
            
        finally:
            # Clean up temporary files
            try:
                if os.path.exists(system_prompt_file):
                    os.unlink(system_prompt_file)
                if os.path.exists(user_prompt_file):
                    os.unlink(user_prompt_file)
            except:
                pass
        
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@app.route('/api/workflow/skip_step', methods=['POST'])
def skip_workflow_step():
    """Skip a workflow step"""
    try:
        data = request.get_json()
        step_name = data.get('step_name')
        iteration = data.get('iteration')
        
        if not step_name:
            return jsonify({'error': 'Step name is required'}), 400
        
        # Get current workflow state
        workflow_state = load_workflow_state()
        current_iteration = iteration if iteration is not None else workflow_state.get('current_iteration')
        
        if current_iteration is None:
            return jsonify({'error': 'Iteration is required'}), 400
        
        # Get database path from config
        search_conf = load_search_conf()
        db_path = search_conf.get('db_path', os.path.join(DATABASES_DIR, 'database.db')) if search_conf else os.path.join(DATABASES_DIR, 'database.db')
        
        # For filter steps, automatically approve all articles in the appropriate stage
        db_manager = DBManager(db_path)
        update_data = []
        articles_updated = 0
        
        if step_name == 'Step 5: Filter by Metadata':
            # Skip metadata filter: approve ALL articles in this iteration (set to METADATA_APPROVED)
            # When skipping this step, we bypass all metadata filtering, so ALL articles are approved
            # This includes articles that were already processed, filtered out, or not yet processed
            articles = db_manager.get_iteration_data(iteration=current_iteration)
            print(f"DEBUG: Found {len(articles)} articles in iteration {current_iteration} to update when skipping metadata filter")
            
            articles_without_id = 0
            articles_already_approved = 0
            
            for article in articles:
                # Skip articles without IDs
                if not article.id or article.id == "":
                    articles_without_id += 1
                    print(f"DEBUG: Skipping article without ID: {getattr(article, 'title', 'Unknown')[:50]}")
                    continue
                    
                # Check current selected status
                current_selected = getattr(article, 'selected', SelectionStage.NOT_SELECTED.value)
                print(f"DEBUG: Article {article.id} ({getattr(article, 'title', 'Unknown')[:50]}): current_selected={current_selected} (type: {type(current_selected)})")
                
                if current_selected is not None:
                    try:
                        current_selected = int(current_selected) if isinstance(current_selected, (str, int)) else SelectionStage.NOT_SELECTED.value
                    except (ValueError, TypeError):
                        current_selected = SelectionStage.NOT_SELECTED.value
                else:
                    current_selected = SelectionStage.NOT_SELECTED.value
                
                print(f"DEBUG: Article {article.id}: parsed current_selected={current_selected}, METADATA_APPROVED.value={SelectionStage.METADATA_APPROVED.value}")
                
                # Update ALL articles to METADATA_APPROVED, regardless of current status
                # This ensures that when skipping, all articles are treated as if they passed metadata filtering
                # Force update even if already METADATA_APPROVED to ensure consistency
                if current_selected < SelectionStage.METADATA_APPROVED.value:
                    update_data.append((article.id, SelectionStage.METADATA_APPROVED.value, "selected"))
                    articles_updated += 1
                    print(f"DEBUG: Adding update for article {article.id} ({getattr(article, 'title', 'Unknown')[:50]}) from selected={current_selected} to METADATA_APPROVED")
                else:
                    articles_already_approved += 1
                    print(f"DEBUG: Article {article.id} already at or above METADATA_APPROVED (selected={current_selected}), skipping selected update")
                
                # Always reset all filtered_out flags to ensure articles aren't excluded
                # Check if flags exist and are truthy (could be 1, True, or "1" from database)
                # Convert to int for comparison since SQLite stores as TEXT
                venue_filtered = getattr(article, 'venue_filtered_out', None)
                print(f"DEBUG: Article {article.id}: venue_filtered_out={venue_filtered} (type: {type(venue_filtered)})")
                if venue_filtered and (venue_filtered == 1 or venue_filtered == "1" or str(venue_filtered).strip() == "1" or venue_filtered is True):
                    update_data.append((article.id, 0, "venue_filtered_out"))
                    print(f"DEBUG: Clearing venue_filtered_out for article {article.id}")
                
                year_filtered = getattr(article, 'year_filtered_out', None)
                print(f"DEBUG: Article {article.id}: year_filtered_out={year_filtered} (type: {type(year_filtered)})")
                if year_filtered and (year_filtered == 1 or year_filtered == "1" or str(year_filtered).strip() == "1" or year_filtered is True):
                    update_data.append((article.id, 0, "year_filtered_out"))
                    print(f"DEBUG: Clearing year_filtered_out for article {article.id}")
                
                language_filtered = getattr(article, 'language_filtered_out', None)
                print(f"DEBUG: Article {article.id}: language_filtered_out={language_filtered} (type: {type(language_filtered)})")
                if language_filtered and (language_filtered == 1 or language_filtered == "1" or str(language_filtered).strip() == "1" or language_filtered is True):
                    update_data.append((article.id, 0, "language_filtered_out"))
                    print(f"DEBUG: Clearing language_filtered_out for article {article.id}")
                
                download_filtered = getattr(article, 'download_filtered_out', None)
                print(f"DEBUG: Article {article.id}: download_filtered_out={download_filtered} (type: {type(download_filtered)})")
                if download_filtered and (download_filtered == 1 or download_filtered == "1" or str(download_filtered).strip() == "1" or download_filtered is True):
                    update_data.append((article.id, 0, "download_filtered_out"))
                    print(f"DEBUG: Clearing download_filtered_out for article {article.id}")
            
            print(f"DEBUG: Summary - Total articles: {len(articles)}, Without ID: {articles_without_id}, Already approved: {articles_already_approved}, To update: {articles_updated}")
            print(f"DEBUG: Total updates prepared: {len(update_data)}, articles_updated: {articles_updated}")
        
        elif step_name == 'Step 6: Filter by Title':
            # Skip title filter: approve all METADATA_APPROVED articles in screening table only
            # When skipping Step 5, we bypass title filtering, so all METADATA_APPROVED articles are approved
            # Only update screening table - iterations table will be updated during solve disagreements
            articles = db_manager.get_iteration_data(
                iteration=current_iteration,
                selected=SelectionStage.METADATA_APPROVED.value
            )
            
            # Get rater name and ensure screening table exists
            rater = os.path.basename(db_path).replace('.db', '') or 'default'
            search_conf = load_search_conf()
            if search_conf:
                annotations = search_conf.get('annotations', [])
                if annotations:
                    db_manager.create_screening_table(annotations)
            
            # Create screening entries for all articles (approve all)
            for article in articles:
                # Insert screening data with keep_title=True (approve all when skipping)
                db_manager.insert_screening_data(
                    article_id=article.id,
                    rater=rater,
                    iteration=current_iteration,
                    keep_title=True,
                    title_reason='Step skipped - all articles approved',
                    keep_content=False,
                    content_reason='',
                    title=article.title
                )
                articles_updated += 1
        
        elif step_name == 'Step 8: Filter by Content':
            # Skip content filter: approve all TITLE_APPROVED articles in screening table only
            # When skipping Step 7, we bypass content filtering, so all TITLE_APPROVED articles are approved
            # Only update screening table - iterations table will be updated during solve disagreements
            articles = db_manager.get_iteration_data(
                iteration=current_iteration,
                selected=SelectionStage.TITLE_APPROVED.value
            )
            
            # Get rater name and ensure screening table exists
            search_conf = load_search_conf()
            rater = search_conf.get('rater', 'default') if search_conf else 'default'
            if search_conf:
                annotations = search_conf.get('annotations', [])
                if annotations:
                    db_manager.create_screening_table(annotations)
            
            # Create screening entries for all articles (approve all when skipping)
            for article in articles:
                # Insert screening data with keep_content=True (approve all when skipping)
                db_manager.insert_screening_data(
                    article_id=article.id,
                    rater=rater,
                    iteration=current_iteration,
                    keep_title=True,  # Assume title was approved if we're at content filtering
                    title_reason='',
                    keep_content=True,
                    content_reason='Step skipped - all articles approved',
                    title=article.title
                )
                articles_updated += 1
        
        # Apply bulk updates if any
        if update_data:
            print(f"DEBUG: Applying {len(update_data)} updates to database")
            db_manager.update_batch_iteration_data(current_iteration, update_data)
            print(f"DEBUG: Updates applied successfully")
        else:
            print(f"DEBUG: No updates to apply (update_data is empty)")
        
        db_manager.conn.close()

        update_workflow_state(
            db_path=db_path,
            current_iteration=current_iteration,
            last_step=step_name,
            skip_step=step_name
        )
        
        # Optionally get next step for reference (not used as last_step anymore)
        next_step = get_next_step_after_skip(step_name)
        
        message = f'Step "{step_name}" has been skipped'
        if articles_updated > 0:
            message += f'. {articles_updated} article(s) automatically approved.'
        
        return jsonify({
            'success': True,
            'message': message,
            'articles_updated': articles_updated,
            'next_step': next_step,  # Optional reference for frontend
            'skipped_steps': load_workflow_state().get('skipped_steps', [])
        })
        
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@app.route('/workflow/solve_title_disagreements', methods=['GET'])
def solve_title_disagreements_page():
    """Page for Step 7: Solve Title Disagreements"""
    search_conf = load_search_conf()
    
    # Get workflow info
    workflow_info = get_workflow_info()
    current_iteration = workflow_info.get('current_iteration') if workflow_info else None
    
    # Default to current iteration if available
    default_iteration = current_iteration if current_iteration is not None else 0
    
    # Get main database path from config
    main_db_path = search_conf.get('db_path', os.path.join(DATABASES_DIR, 'database.db')) if search_conf else os.path.join(DATABASES_DIR, 'database.db')
    
    return render_template('solve_title_disagreements.html',
                         default_iteration=default_iteration,
                         main_db_path=main_db_path,
                         workflow_info=workflow_info)


@app.route('/api/workflow/solve_title_disagreements/get_raters', methods=['POST'])
def get_title_raters():
    """Get list of raters from database files"""
    try:
        data = request.get_json()
        db_paths = data.get('db_paths', [])
        
        if not db_paths or len(db_paths) == 0:
            return jsonify({'error': 'At least 1 database path is required'}), 400
        
        # Validate all databases exist
        for db_path in db_paths:
            if not os.path.exists(db_path):
                return jsonify({'error': f'Database not found: {db_path}'}), 400
        
        # Get raters from each database
        raters_info = []
        for db_path in db_paths:
            try:
                db_manager = DBManager(db_path)
                # Get distinct raters from screening table
                db_manager.cursor.execute("SELECT DISTINCT rater FROM screening WHERE rater IS NOT NULL AND rater != ''")
                raters = [row[0] for row in db_manager.cursor.fetchall()]
                if not raters:
                    # Fallback: try to get rater from search_conf if available
                    search_conf = load_search_conf()
                    rater = search_conf.get('rater', 'default') if search_conf else 'default'
                    raters = [rater]
                
                db_name = os.path.basename(db_path)
                raters_info.append({
                    'db_path': db_path,
                    'db_name': db_name,
                    'raters': raters
                })
            except Exception as e:
                return jsonify({'error': f'Error reading database {db_path}: {str(e)}'}), 400
        
        return jsonify({
            'success': True,
            'raters_info': raters_info
        })
        
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@app.route('/api/workflow/solve_title_disagreements/merge_databases', methods=['POST'])
def merge_title_databases():
    """Merge multiple databases into one - copy all tables from first DB, merge screening tables from all DBs"""
    try:
        data = request.get_json()
        db_paths = data.get('db_paths', [])  # List of database paths
        merged_db_name = data.get('merged_db_name', 'merged_title_screening.db')
        
        if not db_paths or len(db_paths) == 0:
            return jsonify({'error': 'At least 1 database path is required'}), 400
        
        if not merged_db_name:
            return jsonify({'error': 'Merged database name is required'}), 400
        
        # Validate all databases exist
        for db_path in db_paths:
            if not os.path.exists(db_path):
                return jsonify({'error': f'Database not found: {db_path}'}), 400
        
        # Determine merged database path
        merged_db_path = merged_db_name if os.path.isabs(merged_db_name) else os.path.join(os.path.dirname(db_paths[0]), merged_db_name)
        
        # Normalize paths to handle relative/absolute path differences
        normalized_first_db = os.path.normpath(os.path.abspath(db_paths[0]))
        normalized_merged_db = os.path.normpath(os.path.abspath(merged_db_path))
        
        # Only copy if source and destination are different
        if normalized_first_db != normalized_merged_db:
            shutil.copy2(db_paths[0], merged_db_path)
        
        # Open merged database
        merged_db = DBManager(merged_db_path)
        
        # Merge screening tables from all databases
        other_dbs = [DBManager(db_path) for db_path in db_paths[1:]]
        if other_dbs:
            merged_db.merge_databases(*other_dbs)
        
        # Target for write-back is the first DB (the one we copied from)
        target_db_path = os.path.normpath(os.path.abspath(db_paths[0]))
        return jsonify({
            'success': True,
            'merged_db_path': merged_db_path,
            'target_db_path': target_db_path,
            'message': f'Successfully merged {len(db_paths)} database(s) into {merged_db_name}'
        })
        
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@app.route('/api/workflow/solve_title_disagreements/find_disagreements', methods=['POST'])
def find_title_disagreements():
    """Find disagreements from merged database using get_disagreements_screening_data"""
    try:
        data = request.get_json()
        iteration = int(data.get('iteration'))
        merged_db_path = data.get('merged_db_path')
        
        if not merged_db_path:
            return jsonify({'error': 'Merged database path is required'}), 400
        
        if not os.path.exists(merged_db_path):
            return jsonify({'error': f'Merged database not found: {merged_db_path}'}), 400
        
        # Open merged database
        merged_db = DBManager(merged_db_path)
        
        # First, settle agreements (articles where all raters agreed)
        # This automatically updates the iterations table for agreed articles
        try:
            settle_agreements(iteration, merged_db, SelectionStage.TITLE_APPROVED)
        except Exception as e:
            import traceback
            print(f"Error settling agreements: {traceback.format_exc()}")
            # Continue even if there's an error - we still want to find disagreements
        
        # Get disagreements using get_disagreements_screening_data
        try:
            disagreements_raw = merged_db.get_disagreements_screening_data(
                iteration=iteration,
                title_settled=False,
                content_settled=False,
                phase="title"
            )
        except Exception as e:
            import traceback
            print(traceback.format_exc())
            disagreements_raw = []
        
        if not disagreements_raw:
            return jsonify({
                'success': True,
                'disagreements': [],
                'total': 0,
                'message': 'No disagreements found. All raters agreed on all articles.'
            })
        
        # Cluster disagreements by article_id
        clustered_disagreements = {}
        for disagreement in disagreements_raw:
            article_id = disagreement['id']
            if article_id not in clustered_disagreements:
                clustered_disagreements[article_id] = []
            clustered_disagreements[article_id].append(disagreement)
        
        # Get article details from iterations table
        disagreements = []
        for article_id, disagreement_list in clustered_disagreements.items():
            # Get article details
            articles = merged_db.get_iteration_data(iteration=iteration, id=article_id)
            if not articles:
                continue  # Skip if article not found
            
            article = articles[0]
            
            # Organize by rater
            selected_by = []
            filtered_out_by = []
            reasons = {}
            
            for disagreement in disagreement_list:
                rater = disagreement.get('rater', '')
                keep_title = disagreement.get('keep_title', False)
                reason_title = disagreement.get('reason_title', '') or ''
                
                if keep_title:
                    selected_by.append(rater)
                else:
                    filtered_out_by.append(rater)
                
                reasons[rater] = reason_title if reason_title else "No reason provided"
            
            disagreements.append({
                'id': article_id,
                'title': article.title or 'No title',
                'url': article.pub_url or article.eprint_url or '',
                'selected_by': selected_by,
                'filtered_out_by': filtered_out_by,
                'not_selected_by': [],  # Not applicable for merged database
                'reasons': reasons
            })
        
        return jsonify({
            'success': True,
            'disagreements': disagreements,
            'total': len(disagreements)
        })
        
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@app.route('/api/workflow/solve_title_disagreements/get_all_disagreements', methods=['POST'])
def get_all_title_disagreements():
    """Return all title disagreement articles (settled + unsettled) with start_index for first unsettled."""
    try:
        data = request.get_json()
        iteration = int(data.get('iteration'))
        merged_db_path = data.get('merged_db_path')
        if not merged_db_path or not os.path.exists(merged_db_path):
            return jsonify({'error': 'Merged database path is required and must exist'}), 400
        merged_db = DBManager(merged_db_path)
        try:
            settle_agreements(iteration, merged_db, SelectionStage.TITLE_APPROVED)
        except Exception:
            pass
        all_rows = merged_db.get_all_screening_rows_for_iteration(iteration)
        by_id = {}
        for row in all_rows:
            aid = row.get('id')
            if aid not in by_id:
                by_id[aid] = []
            by_id[aid].append(row)
        disagreements_out = []
        for article_id, rows in by_id.items():
            keep_vals = set()
            for r in rows:
                kv = r.get('keep_title')
                keep_vals.add(1 if kv in (1, True, '1') else 0)
            if len(keep_vals) < 2:
                continue
            articles = merged_db.get_iteration_data(iteration=iteration, id=article_id)
            if not articles:
                continue
            article = articles[0]
            selected_by = []
            filtered_out_by = []
            reasons = {}
            any_settled = False
            for r in rows:
                rater = r.get('rater', '')
                keep_title = r.get('keep_title', False)
                if keep_title in (1, True, '1'):
                    selected_by.append(rater)
                else:
                    filtered_out_by.append(rater)
                reasons[rater] = (r.get('reason_title') or '') or 'No reason provided'
                if r.get('title_settled') in (1, True, '1'):
                    any_settled = True
            settled = any_settled
            final_decision = None
            if settled:
                # Read keep_title by column name to avoid schema/order mismatches (e.g. after reload)
                keep_title = merged_db.get_keep_title(article_id, iteration)
                if keep_title is None:
                    keep_title = getattr(article, 'keep_title', None) in (1, True, '1')
                final_decision = 'accept' if keep_title is True else 'reject'
            disagreements_out.append({
                'id': article_id,
                'title': article.title or 'No title',
                'url': article.pub_url or article.eprint_url or '',
                'selected_by': selected_by,
                'filtered_out_by': filtered_out_by,
                'not_selected_by': [],
                'reasons': reasons,
                'settled': settled,
                'final_decision': final_decision,
            })
        # Already-assigned (settled) at the beginning, not-yet-assigned at the end
        disagreements_out.sort(key=lambda d: (0 if d['settled'] else 1))  # 0 first => settled first
        start_index = 0
        while start_index < len(disagreements_out) and disagreements_out[start_index]['settled']:
            start_index += 1
        if start_index >= len(disagreements_out) and disagreements_out:
            start_index = len(disagreements_out) - 1
        return jsonify({
            'success': True,
            'disagreements': disagreements_out,
            'total': len(disagreements_out),
            'start_index': start_index,
        })
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@app.route('/api/workflow/solve_title_disagreements/save_decision', methods=['POST'])
def save_title_disagreement_decision():
    """Save the final decision for a disagreement - updates merged database"""
    try:
        data = request.get_json()
        article_id = data.get('article_id')
        iteration = int(data.get('iteration'))
        decision = data.get('decision')  # 'accept' or 'reject'
        merged_db_path = data.get('merged_db_path')
        
        if not article_id or not decision or not merged_db_path:
            return jsonify({'error': 'Article ID, decision, and merged database path are required'}), 400
        
        if decision not in ['accept', 'reject']:
            return jsonify({'error': 'Decision must be "accept" or "reject"'}), 400
        
        if not os.path.exists(merged_db_path):
            return jsonify({'error': f'Merged database not found: {merged_db_path}'}), 400
        
        # Open merged database
        merged_db = DBManager(merged_db_path)
        
        # Check if article exists
        article_list = merged_db.get_iteration_data(iteration=iteration, id=article_id)
        if not article_list:
            return jsonify({'error': f'Article {article_id} not found in merged database'}), 404
        
        if decision == 'accept':
            # Accept: set to TITLE_APPROVED and keep_title=True
            # Reasonings are stored in screening table and can be accessed later
            merged_db.update_iteration_data(
                iteration,
                article_id,
                selected=SelectionStage.TITLE_APPROVED.value,
                keep_title=True
            )
            # Settle in screening table (title_settled=True, content_settled=False)
            merged_db.settle_screening_data(iteration, article_id, settled=True, phase="title")
        else:
            # Reject: set keep_title flag to False and keep at METADATA_APPROVED (one stage back)
            # Reasonings are stored in screening table and can be accessed later
            merged_db.update_iteration_data(
                iteration,
                article_id,
                selected=SelectionStage.METADATA_APPROVED.value,
                keep_title=False
            )
            # Settle in screening table
            merged_db.settle_screening_data(iteration, article_id, settled=True, phase="title")
        
        # Update workflow state (use merged database path)
        update_workflow_state(
            db_path=merged_db_path,
            current_iteration=iteration,
            last_step="Step 7: Solve Title Disagreements"
        )
        
        return jsonify({
            'success': True,
            'message': f'Decision saved successfully'
        })
        
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@app.route('/api/workflow/solve_title_disagreements/merge_results_back', methods=['POST'])
def merge_title_results_back():
    """Merge all results from merged database back to one of the initial databases"""
    try:
        data = request.get_json()
        merged_db_path = data.get('merged_db_path')
        target_db_path = data.get('target_db_path')
        iteration = int(data.get('iteration'))
        
        if not merged_db_path or not target_db_path:
            return jsonify({'error': 'Merged database path and target database path are required'}), 400
        
        if not os.path.exists(merged_db_path):
            return jsonify({'error': f'Merged database not found: {merged_db_path}'}), 400
        
        if not os.path.exists(target_db_path):
            return jsonify({'error': f'Target database not found: {target_db_path}'}), 400
        
        # Open both databases
        merged_db = DBManager(merged_db_path)
        target_db = DBManager(target_db_path)
        
        # 1. Copy all screening table data from merged to target
        # Get all screening data from merged database
        merged_db.conn.row_factory = sqlite3.Row
        merged_cursor = merged_db.conn.cursor()
        merged_cursor.execute("SELECT * FROM screening WHERE iteration = ?", (iteration,))
        screening_rows = merged_cursor.fetchall()
        
        if screening_rows:
            # Get column names
            column_names = [description[0] for description in merged_cursor.description]
            columns_str = ', '.join(column_names)
            placeholders = ', '.join(['?'] * len(column_names))
            insert_query = f"INSERT OR REPLACE INTO screening ({columns_str}) VALUES ({placeholders})"
            
            # Insert into target database
            for row in screening_rows:
                values = [row[col] for col in column_names]
                target_db.cursor.execute(insert_query, values)
        
        merged_db.conn.row_factory = None
        merged_cursor.close()
        
        # 2. Copy all iterations table updates (selected, keep_title, keep_content) from merged to target
        merged_articles = merged_db.get_iteration_data(iteration=iteration)
        update_data = []
        for article in merged_articles:
            # Get current values from merged database
            selected = getattr(article, 'selected', None)
            keep_title = getattr(article, 'keep_title', None)
            keep_content = getattr(article, 'keep_content', None)
            
            # Check if article exists in target database
            target_articles = target_db.get_iteration_data(iteration=iteration, id=article.id)
            if target_articles:
                # Update existing article
                if selected is not None:
                    update_data.append((article.id, selected, "selected"))
                if keep_title is not None:
                    keep_title_int = 1 if (keep_title == 1 or keep_title == "1" or keep_title is True) else 0
                    update_data.append((article.id, keep_title_int, "keep_title"))
                if keep_content is not None:
                    keep_content_int = 1 if (keep_content == 1 or keep_content == "1" or keep_content is True) else 0
                    update_data.append((article.id, keep_content_int, "keep_content"))
        
        if update_data:
            target_db.update_batch_iteration_data(iteration, update_data)
        
        target_db.conn.commit()
        merged_db.conn.close()
        target_db.conn.close()
        
        # 3. Update workflow state to use target database
        update_workflow_state(
            db_path=target_db_path,
            current_iteration=iteration,
            last_step="Step 7: Solve Title Disagreements"
        )
        
        return jsonify({
            'success': True,
            'message': f'Successfully merged all results from merged database to {target_db_path}'
        })
        
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@app.route('/workflow/solve_content_disagreements', methods=['GET'])
def solve_content_disagreements_page():
    """Page for Step 9: Solve Content Disagreements"""
    search_conf = load_search_conf()
    
    # Get workflow info
    workflow_info = get_workflow_info()
    current_iteration = workflow_info.get('current_iteration') if workflow_info else None
    
    # Default to current iteration if available
    default_iteration = current_iteration if current_iteration is not None else 0
    
    # Get main database path and annotation fields from config
    main_db_path = search_conf.get('db_path', os.path.join(DATABASES_DIR, 'database.db')) if search_conf else os.path.join(DATABASES_DIR, 'database.db')
    annotations_config = search_conf.get('annotations', []) if search_conf else []
    
    return render_template('solve_content_disagreements.html',
                         default_iteration=default_iteration,
                         main_db_path=main_db_path,
                         workflow_info=workflow_info,
                         annotations=annotations_config)


@app.route('/api/workflow/solve_content_disagreements/get_raters', methods=['POST'])
def get_content_raters():
    """Get list of raters from database files"""
    try:
        data = request.get_json()
        db_paths = data.get('db_paths', [])
        
        if not db_paths or len(db_paths) == 0:
            return jsonify({'error': 'At least 1 database path is required'}), 400
        
        # Validate all databases exist
        for db_path in db_paths:
            if not os.path.exists(db_path):
                return jsonify({'error': f'Database not found: {db_path}'}), 400
        
        # Get raters from each database
        raters_info = []
        for db_path in db_paths:
            try:
                db_manager = DBManager(db_path)
                # Get distinct raters from screening table
                db_manager.cursor.execute("SELECT DISTINCT rater FROM screening WHERE rater IS NOT NULL AND rater != ''")
                raters = [row[0] for row in db_manager.cursor.fetchall()]
                if not raters:
                    # Fallback: try to get rater from search_conf if available
                    search_conf = load_search_conf()
                    rater = search_conf.get('rater', 'default') if search_conf else 'default'
                    raters = [rater]
                
                db_name = os.path.basename(db_path)
                raters_info.append({
                    'db_path': db_path,
                    'db_name': db_name,
                    'raters': raters
                })
            except Exception as e:
                return jsonify({'error': f'Error reading database {db_path}: {str(e)}'}), 400
        
        return jsonify({
            'success': True,
            'raters_info': raters_info
        })
        
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@app.route('/api/workflow/solve_content_disagreements/merge_databases', methods=['POST'])
def merge_content_databases():
    """Merge multiple databases into one - copy all tables from first DB, merge screening tables from all DBs"""
    try:
        data = request.get_json()
        db_paths = data.get('db_paths', [])  # List of database paths
        merged_db_name = data.get('merged_db_name', 'merged_content_screening.db')
        
        if not db_paths or len(db_paths) == 0:
            return jsonify({'error': 'At least 1 database path is required'}), 400
        
        if not merged_db_name:
            return jsonify({'error': 'Merged database name is required'}), 400
        
        # Validate all databases exist
        for db_path in db_paths:
            if not os.path.exists(db_path):
                return jsonify({'error': f'Database not found: {db_path}'}), 400
        
        # Determine merged database path
        merged_db_path = merged_db_name if os.path.isabs(merged_db_name) else os.path.join(os.path.dirname(db_paths[0]), merged_db_name)
        
        # Normalize paths to handle relative/absolute path differences
        normalized_first_db = os.path.normpath(os.path.abspath(db_paths[0]))
        normalized_merged_db = os.path.normpath(os.path.abspath(merged_db_path))
        
        # Only copy if source and destination are different
        if normalized_first_db != normalized_merged_db:
            shutil.copy2(db_paths[0], merged_db_path)
        
        # Open merged database
        merged_db = DBManager(merged_db_path)
        
        # Merge screening tables from all databases
        other_dbs = [DBManager(db_path) for db_path in db_paths[1:]]
        if other_dbs:
            merged_db.merge_databases(*other_dbs)
        
        # Ensure annotations table exists in merged DB (for final annotations)
        search_conf = load_search_conf()
        annotations_config = search_conf.get('annotations', []) if search_conf else []
        merged_db.create_annotations_table(annotations_config)
        merged_db.conn.close()
        
        return jsonify({
            'success': True,
            'merged_db_path': merged_db_path,
            'message': f'Successfully merged {len(db_paths)} database(s) into {merged_db_name}'
        })
        
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@app.route('/api/workflow/solve_content_disagreements/find_disagreements', methods=['POST'])
def find_content_disagreements():
    """Find disagreements from merged database using get_disagreements_screening_data"""
    try:
        data = request.get_json()
        iteration = int(data.get('iteration'))
        merged_db_path = data.get('merged_db_path')
        
        if not merged_db_path:
            return jsonify({'error': 'Merged database path is required'}), 400
        
        if not os.path.exists(merged_db_path):
            return jsonify({'error': f'Merged database not found: {merged_db_path}'}), 400
        
        # Open merged database
        merged_db = DBManager(merged_db_path)
        
        # First, settle agreements (articles where all raters agreed)
        # This automatically updates the iterations table for agreed articles
        try:
            settle_agreements(iteration, merged_db, SelectionStage.CONTENT_APPROVED)
        except Exception as e:
            import traceback
            print(f"Error settling agreements: {traceback.format_exc()}")
            # Continue even if there's an error - we still want to find disagreements
        
        # Get all screening rows; include rows with keep_content in (0, 1) regardless of title_settled
        # (content screening can exist without title screening). Only unsettled for find_disagreements.
        try:
            all_rows = merged_db.get_all_screening_rows_for_iteration(iteration)
        except Exception as e:
            import traceback
            print(traceback.format_exc())
            all_rows = []
        
        def _has_keep_content_value(r):
            kv = r.get('keep_content')
            return kv in (0, 1, True, False, '0', '1')
        # Unsettled only, and must have a content decision (0 or 1)
        content_rows = [
            r for r in all_rows
            if _has_keep_content_value(r) and r.get('content_settled') not in (1, True, '1')
        ]
        by_id = {}
        for row in content_rows:
            aid = row.get('id')
            if aid not in by_id:
                by_id[aid] = []
            by_id[aid].append(row)
        
        search_conf = load_search_conf()
        annotations_config = search_conf.get('annotations', []) if search_conf else []
        disagreements = []
        for article_id, disagreement_list in by_id.items():
            keep_vals = set()
            for r in disagreement_list:
                kv = r.get('keep_content')
                if kv in (1, True, '1'):
                    keep_vals.add(1)
                elif kv in (0, False, '0'):
                    keep_vals.add(0)
            if len(keep_vals) < 2:
                continue
            articles = merged_db.get_iteration_data(iteration=iteration, id=article_id)
            if not articles:
                continue
            article = articles[0]
            selected_by = []
            filtered_out_by = []
            reasons = {}
            annotations = {}
            for disagreement in disagreement_list:
                rater = disagreement.get('rater', '')
                keep_content = disagreement.get('keep_content')
                reason_content = disagreement.get('reason_content', '') or ''
                if keep_content in (1, True, '1'):
                    selected_by.append(rater)
                elif keep_content in (0, False, '0'):
                    filtered_out_by.append(rater)
                reasons[rater] = reason_content if reason_content else "No reason provided"
                rater_annotations = {}
                for annotation_key in annotations_config:
                    annotation_value = disagreement.get(annotation_key, '')
                    if annotation_value:
                        rater_annotations[annotation_key] = annotation_value
                if rater_annotations:
                    annotations[rater] = rater_annotations
            disagreements.append({
                'id': article_id,
                'title': article.title or 'No title',
                'url': article.pub_url or article.eprint_url or '',
                'selected_by': selected_by,
                'filtered_out_by': filtered_out_by,
                'not_selected_by': [],
                'reasons': reasons,
                'annotations': annotations
            })
        
        return jsonify({
            'success': True,
            'disagreements': disagreements,
            'total': len(disagreements),
            'message': 'No disagreements found. All raters agreed on all articles.' if not disagreements else None
        })
        
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@app.route('/api/workflow/solve_content_disagreements/get_all_disagreements', methods=['POST'])
def get_all_content_disagreements():
    """Return all content disagreement articles (settled + unsettled) with start_index for first unsettled."""
    try:
        data = request.get_json()
        iteration = int(data.get('iteration'))
        merged_db_path = data.get('merged_db_path')
        if not merged_db_path or not os.path.exists(merged_db_path):
            return jsonify({'error': 'Merged database path is required and must exist'}), 400
        merged_db = DBManager(merged_db_path)
        try:
            settle_agreements(iteration, merged_db, SelectionStage.CONTENT_APPROVED)
        except Exception:
            pass
        all_rows = merged_db.get_all_screening_rows_for_iteration(iteration)
        # Include all rows that have a value in keep_content (1 or 0), regardless of title_settled
        # (raters who did content but not title screening are included)
        def _has_keep_content_value(r):
            kv = r.get('keep_content')
            return kv in (0, 1, True, False, '0', '1')
        content_rows = [r for r in all_rows if _has_keep_content_value(r)]
        by_id = {}
        for row in content_rows:
            aid = row.get('id')
            if aid not in by_id:
                by_id[aid] = []
            by_id[aid].append(row)
        search_conf = load_search_conf()
        annotations_config = search_conf.get('annotations', []) if search_conf else []
        disagreements_out = []
        for article_id, rows in by_id.items():
            keep_vals = set()
            for r in rows:
                kv = r.get('keep_content')
                if kv in (1, True, '1'):
                    keep_vals.add(1)
                elif kv in (0, False, '0'):
                    keep_vals.add(0)
            if len(keep_vals) < 2:
                continue
            articles = merged_db.get_iteration_data(iteration=iteration, id=article_id)
            if not articles:
                continue
            article = articles[0]
            selected_by = []
            filtered_out_by = []
            reasons = {}
            annotations = {}
            any_settled = False
            for r in rows:
                rater = r.get('rater', '')
                keep_content = r.get('keep_content')
                # Only explicit 1 = accept, explicit 0 = reject; no value = not interpreted as rejection
                if keep_content in (1, True, '1'):
                    selected_by.append(rater)
                elif keep_content in (0, False, '0'):
                    filtered_out_by.append(rater)
                reasons[rater] = (r.get('reason_content') or '') or 'No reason provided'
                if r.get('content_settled') in (1, True, '1'):
                    any_settled = True
                rater_annotations = {}
                for annotation_key in annotations_config:
                    v = r.get(annotation_key, '')
                    if v:
                        rater_annotations[annotation_key] = v
                if rater_annotations:
                    annotations[rater] = rater_annotations
            settled = any_settled
            final_decision = None
            if settled:
                keep_content = merged_db.get_keep_content(article_id, iteration)
                if keep_content is None:
                    keep_content = getattr(article, 'keep_content', None) in (1, True, '1')
                final_decision = 'accept' if keep_content is True else 'reject'
            disagreements_out.append({
                'id': article_id,
                'title': article.title or 'No title',
                'url': article.pub_url or article.eprint_url or '',
                'selected_by': selected_by,
                'filtered_out_by': filtered_out_by,
                'not_selected_by': [],
                'reasons': reasons,
                'annotations': annotations,
                'settled': settled,
                'final_decision': final_decision,
            })
        disagreements_out.sort(key=lambda d: (0 if d['settled'] else 1))
        start_index = 0
        while start_index < len(disagreements_out) and disagreements_out[start_index]['settled']:
            start_index += 1
        if start_index >= len(disagreements_out) and disagreements_out:
            start_index = len(disagreements_out) - 1
        return jsonify({
            'success': True,
            'disagreements': disagreements_out,
            'total': len(disagreements_out),
            'start_index': start_index,
        })
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


def _gather_annotations_from_screening(merged_db, article_id: str, iteration: int, annotations_config: list):
    """Collect all annotation values from all raters and join per field (gather all)."""
    rows = merged_db.get_screening_rows_for_article(article_id, iteration)
    result = {}
    for field in annotations_config:
        parts = []
        seen = set()
        for row in rows:
            val = (row.get(field) or "").strip()
            if not val:
                continue
            # Split by comma (and similar) so "a, b" and "b" yield unique tokens
            for part in (p.strip() for p in val.replace(";", ",").split(",") if p.strip()):
                if part not in seen:
                    seen.add(part)
                    parts.append(part)
        result[field] = ", ".join(parts) if parts else ""
    return result


@app.route('/api/workflow/solve_content_disagreements/save_decision', methods=['POST'])
def save_content_disagreement_decision():
    """Save the final decision for a disagreement - updates merged database and optional final annotations table"""
    try:
        data = request.get_json()
        article_id = data.get('article_id')
        iteration = int(data.get('iteration'))
        decision = data.get('decision')  # 'accept' or 'reject'
        merged_db_path = data.get('merged_db_path')
        annotation_handling = data.get('annotation_handling', 'gather_all')  # 'gather_all' | 'manually_select'
        final_annotations = data.get('final_annotations', {})  # for manually_select: { field: value }
        
        if not article_id or not decision or not merged_db_path:
            return jsonify({'error': 'Article ID, decision, and merged database path are required'}), 400
        
        if decision not in ['accept', 'reject']:
            return jsonify({'error': 'Decision must be "accept" or "reject"'}), 400
        
        if not os.path.exists(merged_db_path):
            return jsonify({'error': f'Merged database not found: {merged_db_path}'}), 400
        
        # Open merged database
        merged_db = DBManager(merged_db_path)
        
        # Check if article exists
        article_list = merged_db.get_iteration_data(iteration=iteration, id=article_id)
        if not article_list:
            return jsonify({'error': f'Article {article_id} not found in merged database'}), 404
        
        if decision == 'accept':
            # Accept: set to CONTENT_APPROVED and keep_content=True
            merged_db.update_iteration_data(
                iteration,
                article_id,
                selected=SelectionStage.CONTENT_APPROVED.value,
                keep_content=True
            )
            merged_db.settle_screening_data(iteration, article_id, settled=True, phase="content")
            
            # Write final annotations for accepted article (only if annotations are configured)
            search_conf = load_search_conf()
            annotations_config = search_conf.get('annotations', []) if search_conf else []
            if annotations_config:
                merged_db.create_annotations_table(annotations_config)
                if annotation_handling == 'gather_all':
                    annotation_values = _gather_annotations_from_screening(
                        merged_db, article_id, iteration, annotations_config
                    )
                else:
                    # manually_select: use final_annotations from request; fill missing with empty
                    annotation_values = {
                        k: (final_annotations.get(k) or "").strip()
                        for k in annotations_config
                    }
                merged_db.insert_annotations_data(article_id, iteration, **annotation_values)
        else:
            # Reject: set keep_content flag to False and keep at TITLE_APPROVED
            merged_db.update_iteration_data(
                iteration,
                article_id,
                selected=SelectionStage.TITLE_APPROVED.value,
                keep_content=False
            )
            merged_db.settle_screening_data(iteration, article_id, settled=True, phase="content")
        
        merged_db.conn.close()
        
        # Update workflow state (use merged database path)
        update_workflow_state(
            db_path=merged_db_path,
            current_iteration=iteration,
            last_step="Step 9: Solve Content Disagreements"
        )
        
        return jsonify({
            'success': True,
            'message': f'Decision saved successfully'
        })
        
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@app.route('/api/workflow/solve_content_disagreements/get_agreed_articles', methods=['POST'])
def get_content_agreed_articles():
    """Get articles where all raters agreed to accept (content), for annotation handling."""
    try:
        data = request.get_json()
        iteration = int(data.get('iteration'))
        merged_db_path = data.get('merged_db_path')
        if not merged_db_path or not os.path.exists(merged_db_path):
            return jsonify({'error': 'Merged database path required'}), 400
        merged_db = DBManager(merged_db_path)
        search_conf = load_search_conf()
        annotations_config = search_conf.get('annotations', []) if search_conf else []
        # Rows with content_settled=1; group by article id, keep only where all have keep_content=1
        merged_db.conn.row_factory = sqlite3.Row
        cur = merged_db.conn.cursor()
        cur.execute(
            "SELECT id, keep_content FROM screening WHERE iteration = ? AND content_settled = 1",
            (iteration,)
        )
        rows = cur.fetchall()
        merged_db.conn.row_factory = None
        cur.close()
        # group by id
        by_id = defaultdict(list)
        for row in rows:
            by_id[row[0]].append(1 if row[1] in (1, "1", True) else 0)
        agreed_ids = [aid for aid, keeps in by_id.items() if all(k == 1 for k in keeps)]
        # exclude ids that appear in disagreements (multiple raters but different keep_content)
        try:
            dis = merged_db.get_disagreements_screening_data(
                iteration=iteration, title_settled=True, content_settled=False, phase="content"
            )
            dis_ids = {r["id"] for r in dis}
        except Exception:
            dis_ids = set()
        agreed_ids = [aid for aid in agreed_ids if aid not in dis_ids]
        # build list with article details and annotations per rater
        result = []
        for article_id in agreed_ids:
            articles = merged_db.get_iteration_data(iteration=iteration, id=article_id)
            if not articles:
                continue
            art = articles[0]
            screening_rows = merged_db.get_screening_rows_for_article(article_id, iteration)
            annotations = {}
            for row in screening_rows:
                rater = row.get("rater", "")
                ann = {k: (row.get(k) or "").strip() for k in annotations_config if row.get(k)}
                if ann:
                    annotations[rater] = ann
            result.append({
                "id": article_id,
                "title": art.title or "No title",
                "url": art.pub_url or art.eprint_url or "",
                "annotations": annotations,
            })
        merged_db.conn.close()
        return jsonify({"success": True, "agreed_articles": result})
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return jsonify({"error": str(e)}), 500


@app.route('/api/workflow/solve_content_disagreements/save_annotations_article', methods=['POST'])
def save_annotations_article():
    """Save final annotations for a single article (e.g. when editing agreed article annotations)."""
    try:
        data = request.get_json()
        article_id = data.get('article_id')
        iteration = int(data.get('iteration'))
        merged_db_path = data.get('merged_db_path')
        final_annotations = data.get('final_annotations', {})
        if not article_id or not merged_db_path or not os.path.exists(merged_db_path):
            return jsonify({'error': 'Article ID and merged database path required'}), 400
        search_conf = load_search_conf()
        annotations_config = search_conf.get('annotations', []) if search_conf else []
        if not annotations_config:
            return jsonify({"success": True, "message": "No annotation fields configured"})
        merged_db = DBManager(merged_db_path)
        merged_db.create_annotations_table(annotations_config)
        annotation_values = {k: (final_annotations.get(k) or "").strip() for k in annotations_config}
        merged_db.insert_annotations_data(article_id, iteration, **annotation_values)
        merged_db.conn.close()
        return jsonify({"success": True, "message": "Annotations saved"})
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return jsonify({"error": str(e)}), 500


@app.route('/api/workflow/solve_content_disagreements/apply_annotations_agreed', methods=['POST'])
def apply_annotations_agreed():
    """Apply gather-all annotations to agreed (already accepted) articles."""
    try:
        data = request.get_json()
        iteration = int(data.get('iteration'))
        merged_db_path = data.get('merged_db_path')
        article_ids = data.get('article_ids', [])  # if empty, apply to all agreed
        if not merged_db_path or not os.path.exists(merged_db_path):
            return jsonify({'error': 'Merged database path required'}), 400
        search_conf = load_search_conf()
        annotations_config = search_conf.get('annotations', []) if search_conf else []
        if not annotations_config:
            return jsonify({"success": True, "applied": 0, "message": "No annotation fields configured"})
        merged_db = DBManager(merged_db_path)
        merged_db.create_annotations_table(annotations_config)
        if not article_ids:
            # discover agreed article ids (same logic as get_agreed_articles)
            merged_db.conn.row_factory = sqlite3.Row
            cur = merged_db.conn.cursor()
            cur.execute(
                "SELECT id, keep_content FROM screening WHERE iteration = ? AND content_settled = 1",
                (iteration,)
            )
            rows = cur.fetchall()
            by_id = defaultdict(list)
            for row in rows:
                by_id[row[0]].append(1 if row[1] in (1, "1", True) else 0)
            agreed_ids = [aid for aid, keeps in by_id.items() if all(k == 1 for k in keeps)]
            try:
                dis = merged_db.get_disagreements_screening_data(
                    iteration=iteration, title_settled=True, content_settled=False, phase="content"
                )
                dis_ids = {r["id"] for r in dis}
            except Exception:
                dis_ids = set()
            article_ids = [aid for aid in agreed_ids if aid not in dis_ids]
            merged_db.conn.row_factory = None
            cur.close()
        applied = 0
        for article_id in article_ids:
            annotation_values = _gather_annotations_from_screening(
                merged_db, article_id, iteration, annotations_config
            )
            merged_db.insert_annotations_data(article_id, iteration, **annotation_values)
            applied += 1
        merged_db.conn.close()
        return jsonify({"success": True, "applied": applied, "message": f"Applied annotations to {applied} article(s)"})
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return jsonify({"error": str(e)}), 500


@app.route('/api/workflow/solve_content_disagreements/merge_results_back', methods=['POST'])
def merge_content_results_back():
    """Merge all results from merged database back to one of the initial databases"""
    try:
        data = request.get_json()
        merged_db_path = data.get('merged_db_path')
        target_db_path = data.get('target_db_path')
        iteration = int(data.get('iteration'))
        
        if not merged_db_path or not target_db_path:
            return jsonify({'error': 'Merged database path and target database path are required'}), 400
        
        if not os.path.exists(merged_db_path):
            return jsonify({'error': f'Merged database not found: {merged_db_path}'}), 400
        
        if not os.path.exists(target_db_path):
            return jsonify({'error': f'Target database not found: {target_db_path}'}), 400
        
        # Open both databases
        merged_db = DBManager(merged_db_path)
        target_db = DBManager(target_db_path)
        
        # 1. Copy all screening table data from merged to target
        # Get all screening data from merged database
        merged_db.conn.row_factory = sqlite3.Row
        merged_cursor = merged_db.conn.cursor()
        merged_cursor.execute("SELECT * FROM screening WHERE iteration = ?", (iteration,))
        screening_rows = merged_cursor.fetchall()
        
        if screening_rows:
            # Get column names
            column_names = [description[0] for description in merged_cursor.description]
            columns_str = ', '.join(column_names)
            placeholders = ', '.join(['?'] * len(column_names))
            insert_query = f"INSERT OR REPLACE INTO screening ({columns_str}) VALUES ({placeholders})"
            
            # Insert into target database
            for row in screening_rows:
                values = [row[col] for col in column_names]
                target_db.cursor.execute(insert_query, values)
        
        merged_db.conn.row_factory = None
        merged_cursor.close()
        
        # 2. Copy all iterations table updates (selected, keep_title, keep_content) from merged to target
        merged_articles = merged_db.get_iteration_data(iteration=iteration)
        update_data = []
        for article in merged_articles:
            # Get current values from merged database
            selected = getattr(article, 'selected', None)
            keep_title = getattr(article, 'keep_title', None)
            keep_content = getattr(article, 'keep_content', None)
            
            # Check if article exists in target database
            target_articles = target_db.get_iteration_data(iteration=iteration, id=article.id)
            if target_articles:
                # Update existing article
                if selected is not None:
                    update_data.append((article.id, selected, "selected"))
                if keep_title is not None:
                    keep_title_int = 1 if (keep_title == 1 or keep_title == "1" or keep_title is True) else 0
                    update_data.append((article.id, keep_title_int, "keep_title"))
                if keep_content is not None:
                    keep_content_int = 1 if (keep_content == 1 or keep_content == "1" or keep_content is True) else 0
                    update_data.append((article.id, keep_content_int, "keep_content"))
        
        if update_data:
            target_db.update_batch_iteration_data(iteration, update_data)
        
        # 3. Copy annotations table (final annotations) from merged to target for this iteration
        search_conf = load_search_conf()
        annotations_config = search_conf.get('annotations', []) if search_conf else []
        target_db.create_annotations_table(annotations_config)
        try:
            merged_db.conn.row_factory = sqlite3.Row
            ann_cursor = merged_db.conn.cursor()
            ann_cursor.execute("SELECT * FROM annotations WHERE iteration = ?", (iteration,))
            ann_rows = ann_cursor.fetchall()
            if ann_rows:
                ann_columns = [d[0] for d in ann_cursor.description]
                ann_cols_str = ', '.join(ann_columns)
                ann_placeholders = ', '.join(['?'] * len(ann_columns))
                ann_insert = f"INSERT OR REPLACE INTO annotations ({ann_cols_str}) VALUES ({ann_placeholders})"
                for row in ann_rows:
                    target_db.cursor.execute(ann_insert, [row[col] for col in ann_columns])
            merged_db.conn.row_factory = None
            ann_cursor.close()
        except sqlite3.OperationalError:
            # Annotations table may not exist in merged DB if no annotations config
            pass
        
        target_db.conn.commit()
        merged_db.conn.close()
        target_db.conn.close()
        
        # 4. Update workflow state to use target database
        update_workflow_state(
            db_path=target_db_path,
            current_iteration=iteration,
            last_step="Step 9: Solve Content Disagreements"
        )
        
        return jsonify({
            'success': True,
            'message': f'Successfully merged all results from merged database to {target_db_path}'
        })
        
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@app.route('/workflow/remove_duplicates', methods=['GET'])
def remove_duplicates_page():
    """Page for Step 2: Remove Duplicates"""
    # Get workflow info
    workflow_info = get_workflow_info()
    current_iteration = workflow_info.get('current_iteration') if workflow_info else None
    
    # Default to current iteration if available
    default_iteration = current_iteration if current_iteration is not None else 0
    
    return render_template('remove_duplicates.html',
                         default_iteration=default_iteration,
                         workflow_info=workflow_info)


@app.route('/api/workflow/remove_duplicates/find', methods=['POST'])
def find_duplicates():
    """Find duplicate articles based on title similarity"""
    try:
        from difflib import SequenceMatcher
        
        data = request.get_json()
        iterations = data.get('iterations', [])
        similarity_threshold = float(data.get('similarity_threshold', 0.8))
        
        if not iterations:
            return jsonify({'error': 'At least one iteration is required'}), 400
        
        if similarity_threshold < 0 or similarity_threshold > 1:
            return jsonify({'error': 'Similarity threshold must be between 0.0 and 1.0'}), 400
        
        # Get database path from config
        search_conf = load_search_conf()
        if not search_conf or 'db_path' not in search_conf:
            return jsonify({'error': 'Database path not configured'}), 400
        
        db_path = search_conf['db_path']
        db_manager = DBManager(db_path)
        
        # Expand iterations: if user selects iteration n, include all iterations from 0 to n
        # Remove duplicates and sort
        expanded_iterations = set()
        for iteration in iterations:
            # For each selected iteration, include all iterations from 0 to that iteration
            for i in range(0, iteration + 1):
                expanded_iterations.add(i)
        expanded_iterations = sorted(list(expanded_iterations))
        
        # Fetch articles from all expanded iterations
        total_articles = []
        for iteration in expanded_iterations:
            articles = db_manager.get_iteration_data(
                iteration=iteration,
                selected=SelectionStage.CONTENT_APPROVED
            )
            total_articles.extend(articles)
        
        # Find duplicate candidates
        duplicates = []
        processed = set()
        
        def calculate_title_similarity(title1: str, title2: str) -> float:
            """Calculate similarity between two titles"""
            norm_title1 = title1.lower().strip() if title1 else ''
            norm_title2 = title2.lower().strip() if title2 else ''
            return SequenceMatcher(None, norm_title1, norm_title2).ratio()
        
        for i, article1 in enumerate(total_articles):
            if article1.id in processed:
                continue
            
            for j, article2 in enumerate(total_articles[i+1:], i+1):
                if article2.id in processed:
                    continue
                
                similarity = calculate_title_similarity(article1.title, article2.title)
                
                if similarity >= similarity_threshold:
                    # Convert articles to dict for JSON serialization
                    article1_dict = {
                        'id': article1.id,
                        'title': article1.title or '',
                        'authors': article1.authors or '',
                        'venue': article1.venue or '',
                        'pub_year': article1.pub_year or 0,
                        'pub_url': article1.pub_url or '',
                        'eprint_url': article1.eprint_url or '',
                        'num_citations': getattr(article1, 'num_citations', -1),
                        'iteration': article1.iteration
                    }
                    article2_dict = {
                        'id': article2.id,
                        'title': article2.title or '',
                        'authors': article2.authors or '',
                        'venue': article2.venue or '',
                        'pub_year': article2.pub_year or 0,
                        'pub_url': article2.pub_url or '',
                        'eprint_url': article2.eprint_url or '',
                        'num_citations': getattr(article2, 'num_citations', -1),
                        'iteration': article2.iteration
                    }
                    
                    duplicates.append({
                        'article1': article1_dict,
                        'article2': article2_dict,
                        'similarity': similarity
                    })
                    
                    processed.add(article1.id)
                    processed.add(article2.id)
                    break
        
        return jsonify({
            'success': True,
            'duplicates': duplicates,
            'total': len(duplicates)
        })
        
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@app.route('/api/workflow/remove_duplicates/save_decision', methods=['POST'])
def save_duplicate_decision():
    """Save the duplicate resolution decision"""
    try:
        data = request.get_json()
        article1_id = data.get('article1_id')
        article2_id = data.get('article2_id')
        article1_iteration = int(data.get('article1_iteration'))
        article2_iteration = int(data.get('article2_iteration'))
        decision = data.get('decision')  # 'keep_article1', 'keep_article2', or 'keep_both'
        keep_id = data.get('keep_id')
        remove_id = data.get('remove_id')
        
        if not article1_id or not article2_id or not decision:
            return jsonify({'error': 'Article IDs and decision are required'}), 400
        
        if decision not in ['keep_article1', 'keep_article2', 'keep_both']:
            return jsonify({'error': 'Invalid decision'}), 400
        
        # Get database path from config
        search_conf = load_search_conf()
        if not search_conf or 'db_path' not in search_conf:
            return jsonify({'error': 'Database path not configured'}), 400
        
        db_path = search_conf['db_path']
        db_manager = DBManager(db_path)
        
        if decision == 'keep_both':
            # Don't mark anything as duplicate
            pass
        else:
            # Mark the article to remove as duplicate
            if remove_id:
                remove_iteration = article2_iteration if remove_id == article2_id else article1_iteration
                db_manager.update_iteration_data(
                    iteration=remove_iteration,
                    article_id=remove_id,
                    selected=SelectionStage.DUPLICATE.value
                )
        
        # Update workflow state
        update_workflow_state(
            db_path=db_path,
            current_iteration=None,  # Don't change iteration
            last_step="Step 2: Remove Duplicates"
        )
        
        return jsonify({
            'success': True,
            'message': 'Decision saved successfully'
        })
        
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@app.route('/workflow/generate_csv', methods=['GET'])
def generate_csv_page():
    """Page for Step 10: Generate CSV"""
    search_conf = load_search_conf()
    
    # Get workflow info
    workflow_info = get_workflow_info()
    current_iteration = workflow_info.get('current_iteration') if workflow_info else None
    
    # Default to current iteration if available
    default_iteration = current_iteration if current_iteration is not None else 0
    
    # Get CSV path from config
    csv_path = search_conf.get('csv_path', 'results.csv') if search_conf else 'results.csv'
    
    return render_template('generate_csv.html',
                         default_iteration=default_iteration,
                         csv_path=csv_path,
                         workflow_info=workflow_info)


@app.route('/api/workflow/generate_csv/export', methods=['POST'])
def generate_csv_export():
    """Generate CSV file with content-approved articles"""
    try:
        import pandas as pd
        import os
        
        data = request.get_json()
        iterations = data.get('iterations', [])
        suggested_filename = data.get('output_path', 'results.csv')  # Used for download filename suggestion
        
        if not iterations:
            return jsonify({'error': 'At least one iteration is required'}), 400
        
        # Get database path from config
        search_conf = load_search_conf()
        if not search_conf or 'db_path' not in search_conf:
            return jsonify({'error': 'Database path not configured'}), 400
        
        db_path = search_conf['db_path']
        db_manager = DBManager(db_path)
        
        # Fetch articles from all specified iterations
        article_data = []
        for iteration in iterations:
            articles = db_manager.get_iteration_data(
                iteration=iteration,
                selected=SelectionStage.CONTENT_APPROVED
            )
            
            for article in articles:
                # Parse content_reason to extract reason and annotations
                content_reason = getattr(article, 'content_reason', '') or ''
                reason_text = ''
                annotations_dict = {}
                
                if content_reason:
                    try:
                        reason_json = json.loads(content_reason)
                        if isinstance(reason_json, dict):
                            reason_text = reason_json.get('reason', '')
                            # Extract annotations (everything except 'reason')
                            for key, value in reason_json.items():
                                if key != 'reason':
                                    annotations_dict[key] = value
                    except (json.JSONDecodeError, TypeError):
                        # Not JSON, treat as plain text reason
                        reason_text = content_reason
                
                # Parse authors if it's JSON
                authors_text = article.authors or ''
                if authors_text:
                    try:
                        authors_json = json.loads(authors_text)
                        if isinstance(authors_json, list):
                            authors_text = ', '.join([a.get('name', a) if isinstance(a, dict) else str(a) for a in authors_json])
                    except (json.JSONDecodeError, TypeError):
                        pass  # Use as-is
                
                # Get URL (prefer pub_url, fallback to eprint_url)
                url = article.pub_url or article.eprint_url or ''
                
                # Build row data
                row_data = {
                    'title': article.title or '',
                    'authors': authors_text,
                    'year': article.pub_year or '',
                    'venue': article.venue or '',
                    'citations': getattr(article, 'num_citations', -1) if int(getattr(article, 'num_citations', -1)) >= 0 else '',
                    'url': url,
                    'iteration': iteration,
                    'reason': reason_text
                }
                
                # Add annotation fields
                for key, value in annotations_dict.items():
                    row_data[key] = value
                
                article_data.append(row_data)
        
        # Create DataFrame and save to CSV
        if not article_data:
            return jsonify({
                'success': False,
                'error': 'No content-approved articles found in the specified iterations'
            }), 400
        
        df = pd.DataFrame(article_data)
        
        # Save CSV to uploads directory for web access only
        uploads_dir = 'uploads'
        os.makedirs(uploads_dir, exist_ok=True)
        
        # Generate a unique filename based on timestamp
        import time
        timestamp = int(time.time())
        
        # Use suggested filename if provided, otherwise default
        if suggested_filename and suggested_filename.endswith('.csv'):
            base_name = suggested_filename[:-4]  # Remove .csv extension
            filename = f'{base_name}_{timestamp}.csv'
        else:
            filename = f'results_{timestamp}.csv'
        
        csv_path_in_uploads = os.path.join(uploads_dir, filename)
        
        # Save CSV to uploads directory (only location where file is saved)
        df.to_csv(csv_path_in_uploads, index=False)
        
        # Update workflow state
        update_workflow_state(
            db_path=db_path,
            current_iteration=None,  # Don't change iteration
            last_step="Step 10: Generate CSV"
        )
        
        # Return download URL using Flask route
        download_url = f'/api/workflow/generate_csv/download/{filename}'
        
        return jsonify({
            'success': True,
            'csv_filename': filename,
            'article_count': len(article_data),
            'download_url': download_url
        })
        
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@app.route('/api/workflow/generate_csv/download/<filename>')
def download_csv(filename):
    """Serve CSV file for download"""
    try:
        import os
        
        # Ensure filename is safe (no path traversal)
        filename = os.path.basename(filename)
        csv_path = os.path.join('uploads', filename)
        
        if not os.path.exists(csv_path):
            return jsonify({'error': 'CSV file not found'}), 404
        
        return send_file(
            csv_path,
            mimetype='text/csv',
            as_attachment=True,
            download_name=filename
        )
        
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


# ============================================================================
# Analysis Tools Routes (for final paper analysis after snowballing)
# ============================================================================

@app.route('/analysis/generate_config', methods=['GET', 'POST'])
def generate_analysis_conf_route():
    """Generate analysis and LLM configuration"""
    if request.method == 'GET':
        # Check if analysis_conf.json already exists
        analysis_conf_exists = os.path.exists(ANALYSIS_CONF_PATH)
        
        # Try to load existing config if it exists
        analysis_conf = None
        if analysis_conf_exists:
            try:
                with open(ANALYSIS_CONF_PATH, 'r') as f:
                    analysis_conf = json.load(f)
            except:
                pass
        
        # Check if llm_config.json exists
        llm_config_exists = os.path.exists(LLM_CONFIG_PATH)
        
        # Try to load existing LLM config if it exists
        llm_config = None
        if llm_config_exists:
            try:
                with open(LLM_CONFIG_PATH, 'r') as f:
                    llm_config = json.load(f)
            except:
                pass
        
        # Get default CSV path from search_conf if available
        search_conf = load_search_conf()
        default_csv_path = search_conf.get('csv_path', 'results.csv') if search_conf else 'results.csv'
        
        return render_template('generate_analysis_conf.html',
                             analysis_conf_exists=analysis_conf_exists,
                             analysis_conf=analysis_conf,
                             llm_config_exists=llm_config_exists,
                             llm_config=llm_config,
                             default_csv_path=default_csv_path)
    
    # Handle POST request
    try:
        # Get form data
        form_data = request.form.to_dict()
        
        # Validate and save Analysis Configuration
        required_fields = ['articles_folder', 'csv_path', 'seed_file', 'output_path', 'topics_file']
        for field in required_fields:
            if not form_data.get(field) or not form_data.get(field).strip():
                flash(f'{field.replace("_", " ").title()} is required', 'error')
                return redirect(url_for('generate_analysis_conf_route'))
        
        analysis_config = {
            'articles_folder': form_data.get('articles_folder').strip(),
            'csv_path': form_data.get('csv_path').strip(),
            'seed_file': form_data.get('seed_file').strip(),
            'output_path': form_data.get('output_path').strip(),
            'topics_file': form_data.get('topics_file').strip()
        }
        
        # Save analysis config to file
        # Ensure confs directory exists
        os.makedirs(CONFS_DIR, exist_ok=True)
        with open(ANALYSIS_CONF_PATH, 'w') as f:
            json.dump(analysis_config, f, indent=4)
        
        # Build and save LLM Configuration
        # Validate required LLM fields
        required_llm_models = ['openai_model', 'gemini_model', 'anthropic_model']
        for model_field in required_llm_models:
            if not form_data.get(model_field) or not form_data.get(model_field).strip():
                flash(f'{model_field.replace("_", " ").title()} is required', 'error')
                return redirect(url_for('generate_analysis_conf_route'))
        
        # Load existing LLM config to preserve pricing values
        existing_llm_config = None
        if os.path.exists(LLM_CONFIG_PATH):
            try:
                with open(LLM_CONFIG_PATH, 'r') as f:
                    existing_llm_config = json.load(f)
            except:
                pass
        
        # Helper function to get pricing or default
        def get_pricing(provider, existing_config):
            if existing_config and provider in existing_config:
                pricing = existing_config[provider].get('pricing_per_1k_tokens')
                if pricing:
                    return pricing
            # Default pricing values
            defaults = {
                'openai': {'input': 0.0015, 'output': 0.002},
                'gemini': {'input': 0.0005, 'output': 0.0015},
                'anthropic': {'input': 0.003, 'output': 0.015}
            }
            return defaults.get(provider, {'input': 0.0, 'output': 0.0})
        
        llm_config = {
            'openai': {
                'api_key': form_data.get('openai_api_key', '').strip(),
                'api_key_env': form_data.get('openai_api_key_env', 'OPENAI_API_KEY').strip(),
                'model': form_data.get('openai_model').strip(),
                'temperature': float(form_data.get('openai_temperature', 1.0)),
                'max_output_tokens': int(form_data.get('openai_max_output_tokens', 5000)),
                'context_length': int(form_data.get('openai_context_length', 20000)),
                'pricing_per_1k_tokens': get_pricing('openai', existing_llm_config)
            },
            'gemini': {
                'api_key': form_data.get('gemini_api_key', '').strip(),
                'api_key_env': form_data.get('gemini_api_key_env', 'GEMINI_API_KEY').strip(),
                'model': form_data.get('gemini_model').strip(),
                'temperature': float(form_data.get('gemini_temperature', 0.7)),
                'max_output_tokens': int(form_data.get('gemini_max_output_tokens', 5000)),
                'context_length': int(form_data.get('gemini_context_length', 16385)),
                'pricing_per_1k_tokens': get_pricing('gemini', existing_llm_config)
            },
            'anthropic': {
                'api_key': form_data.get('anthropic_api_key', '').strip(),
                'api_key_env': form_data.get('anthropic_api_key_env', 'ANTHROPIC_API_KEY').strip(),
                'model': form_data.get('anthropic_model').strip(),
                'temperature': float(form_data.get('anthropic_temperature', 0.7)),
                'max_output_tokens': int(form_data.get('anthropic_max_output_tokens', 5000)),
                'context_length': int(form_data.get('anthropic_context_length', 16385)),
                'pricing_per_1k_tokens': get_pricing('anthropic', existing_llm_config)
            }
        }
        
        # Ensure the directory exists before saving
        llm_config_dir = os.path.dirname(LLM_CONFIG_PATH)
        if llm_config_dir and not os.path.exists(llm_config_dir):
            os.makedirs(llm_config_dir, exist_ok=True)
        
        # Save LLM config to file
        with open(LLM_CONFIG_PATH, 'w') as f:
            json.dump(llm_config, f, indent=4)
        
        flash('All configurations saved successfully!', 'success')
        return redirect(url_for('generate_analysis_conf_route'))
        
    except ValueError as e:
        flash(f'Validation error: {str(e)}', 'error')
        return redirect(url_for('generate_analysis_conf_route'))
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        flash(f'Error saving configuration: {str(e)}', 'error')
        return redirect(url_for('generate_analysis_conf_route'))


@app.route('/analysis/download_pdfs', methods=['GET'])
def download_pdfs_analysis_page():
    """Page for downloading PDFs from CSV for analysis"""
    # Check if analysis_conf exists and get CSV path from there first
    analysis_conf_exists = os.path.exists(ANALYSIS_CONF_PATH)
    analysis_conf = None
    csv_path = 'results.csv'
    
    if analysis_conf_exists:
        try:
            with open(ANALYSIS_CONF_PATH, 'r') as f:
                analysis_conf = json.load(f)
                csv_path = analysis_conf.get('csv_path', 'results.csv')
        except:
            pass
    
    # Fall back to search_conf if analysis_conf doesn't have csv_path
    if not csv_path or csv_path == 'results.csv':
        search_conf = load_search_conf()
        csv_path = search_conf.get('csv_path', 'results.csv') if search_conf else 'results.csv'
    
    return render_template('download_pdfs_analysis.html',
                         csv_path=csv_path,
                         analysis_conf=analysis_conf)


@app.route('/api/analysis/download_pdfs', methods=['POST'])
def download_pdfs_analysis_api():
    """API endpoint to download PDFs from CSV"""
    try:
        data = request.get_json()
        csv_path = data.get('csv_path', '').strip()
        articles_folder = data.get('articles_folder', 'articles').strip()
        
        if not csv_path:
            return jsonify({'error': 'CSV path is required'}), 400
        
        if not os.path.exists(csv_path):
            return jsonify({'error': f'CSV file not found: {csv_path}'}), 400
        
        # Import download functions
        from utils.article_processing.download_pdfs import download_pdf, is_valid_pdf
        import csv
        import pathlib
        import time
        
        # Create output folder
        pathlib.Path(articles_folder).mkdir(parents=True, exist_ok=True)
        
        failed_downloads = []
        success_count = 0
        total_count = 0
        
        # Read CSV and process each row
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            
            for row in reader:
                total_count += 1
                title = row.get('title', '').strip()
                url = row.get('url', '').strip()
                
                if not title or not url:
                    continue
                
                # Generate filename from title (same logic as in scripts)
                article_id = title.replace(" ", "_").replace(":", "_").replace("/", "_").replace("\\", "_").replace("*", "_").replace("?", "_").replace("\"", "_").replace("<", "_").replace(">", "_").replace(".", "_")
                filename = f"{article_id}.pdf"
                output_file = os.path.join(articles_folder, filename)
                
                # Skip if already exists
                if os.path.exists(output_file):
                    success_count += 1
                    continue
                
                # Try to download
                if download_pdf(url, output_file):
                    # Validate PDF
                    if is_valid_pdf(output_file):
                        success_count += 1
                        time.sleep(1)  # Rate limiting
                    else:
                        # Invalid PDF, remove it
                        if os.path.exists(output_file):
                            os.remove(output_file)
                        failed_downloads.append({
                            'title': title,
                            'url': url,
                            'filename': filename
                        })
                else:
                    failed_downloads.append({
                        'title': title,
                        'url': url,
                        'filename': filename
                    })
        
        return jsonify({
            'success': True,
            'success_count': success_count,
            'total_count': total_count,
            'failed_downloads': failed_downloads
        })
        
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@app.route('/analysis/task_assistant', methods=['GET'])
def task_assistant_page():
    """Page for task assistant (Q&A system for PDFs)"""
    # Check if analysis_conf exists
    analysis_conf_exists = os.path.exists(ANALYSIS_CONF_PATH)
    analysis_conf = None
    csv_path = 'results.csv'
    
    if analysis_conf_exists:
        try:
            with open(ANALYSIS_CONF_PATH, 'r') as f:
                analysis_conf = json.load(f)
                csv_path = analysis_conf.get('csv_path', 'results.csv')
        except:
            pass
    
    # Fall back to search_conf if needed
    if not csv_path or csv_path == 'results.csv':
        search_conf = load_search_conf()
        csv_path = search_conf.get('csv_path', 'results.csv') if search_conf else 'results.csv'
    
    return render_template('task_assistant.html',
                         analysis_conf=analysis_conf,
                         csv_path=csv_path)


@app.route('/api/analysis/task_assistant', methods=['POST'])
def task_assistant_api():
    """API endpoint to run task assistant on PDFs"""
    try:
        data = request.get_json()
        articles_folder = data.get('articles_folder', '').strip()
        csv_path = data.get('csv_path', '').strip()
        question = data.get('question', '').strip()
        provider = data.get('provider', 'openai').strip()
        output_path = data.get('output_path', 'output/task_assistant_results.csv').strip()
        
        if not articles_folder:
            return jsonify({'error': 'Articles folder is required'}), 400
        if not csv_path:
            return jsonify({'error': 'CSV path is required'}), 400
        if not question:
            return jsonify({'error': 'Question is required'}), 400
        if not os.path.exists(articles_folder):
            return jsonify({'error': f'Articles folder not found: {articles_folder}'}), 400
        if not os.path.exists(csv_path):
            return jsonify({'error': f'CSV file not found: {csv_path}'}), 400
        
        # Import required modules
        from utils.article_llm_analysis.task_assistant import PDFQASystem
        from utils.article_processing.shared_utils import load_config, create_llm, get_use_chat_model
        from pathlib import Path
        import csv
        
        # Load LLM config
        if not os.path.exists(LLM_CONFIG_PATH):
            return jsonify({'error': f'LLM config file not found: {LLM_CONFIG_PATH}'}), 400
        
        config = load_config(LLM_CONFIG_PATH)
        if provider not in config:
            return jsonify({'error': f'Provider {provider} not found in LLM config'}), 400
        
        provider_config = config[provider]
        
        # Create LLM
        llm = create_llm(provider, provider_config)
        use_chat_model = get_use_chat_model(provider)
        max_output_tokens = provider_config.get("max_output_tokens", 5000)
        context_length = provider_config.get("context_length", 16385)
        
        # Initialize QA system
        qa_system = PDFQASystem(
            llm,
            use_chat_model,
            context_length,
            max_output_tokens,
            provider,
            provider_config["model"],
            provider_config,
        )
        
        # Load CSV to create title mapping (sanitized title -> original title)
        title_map = {}
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                title = row.get('title', '').strip()
                if title:
                    # Sanitize title same way as in download_pdfs
                    sanitized = title.replace(" ", "_").replace(":", "_").replace("/", "_").replace("\\", "_").replace("*", "_").replace("?", "_").replace("\"", "_").replace("<", "_").replace(">", "_").replace(".", "_")
                    title_map[sanitized] = title
        
        # Get all PDF files
        pdf_folder = Path(articles_folder)
        pdf_files = list(pdf_folder.glob("*.pdf"))
        
        if not pdf_files:
            return jsonify({'error': f'No PDF files found in {articles_folder}'}), 400
        
        # Process each PDF
        results = []
        for pdf_file in pdf_files:
            pdf_name = pdf_file.stem  # filename without .pdf extension
            
            # Get original title from map, or use filename if not found
            article_title = title_map.get(pdf_name, pdf_name.replace('_', ' '))
            
            # Ask question about this PDF
            response = qa_system.ask_single_prompt(str(pdf_file), question)
            
            results.append({
                'article_id': f"{pdf_name}.pdf",
                'article_title': article_title,
                'answer': response.get('answer', 'Error processing PDF')
            })
        
        # Ensure output directory exists
        output_dir = os.path.dirname(output_path)
        if output_dir and not os.path.exists(output_dir):
            os.makedirs(output_dir, exist_ok=True)
        
        # Save results to CSV
        with open(output_path, 'w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=['article_id', 'article_title', 'answer'])
            writer.writeheader()
            writer.writerows(results)
        
        return jsonify({
            'success': True,
            'output_path': output_path,
            'processed_count': len(results)
        })
        
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@app.route('/analysis/topic_modeling', methods=['GET'])
def topic_modeling_page():
    """Page for topic modeling"""
    # Check if analysis_conf exists
    analysis_conf_exists = os.path.exists(ANALYSIS_CONF_PATH)
    analysis_conf = None
    if analysis_conf_exists:
        try:
            with open(ANALYSIS_CONF_PATH, 'r') as f:
                analysis_conf = json.load(f)
        except:
            pass
    
    return render_template('topic_modeling.html',
                         analysis_conf=analysis_conf)


@app.route('/api/analysis/topic_modeling', methods=['POST'])
def topic_modeling_api():
    """API endpoint to execute topic modeling steps"""
    try:
        data = request.get_json()
        step = data.get('step', '').strip()
        articles_folder = data.get('articles_folder', '').strip()
        output_dir = data.get('output_dir', '').strip()
        provider = data.get('provider', 'openai').strip()
        seed_file = data.get('seed_file', '').strip()
        
        if not step:
            return jsonify({'error': 'Step is required'}), 400
        if step not in ['level1', 'level2', 'assign', 'refine', 'correct']:
            return jsonify({'error': f'Invalid step: {step}'}), 400
        if not articles_folder:
            return jsonify({'error': 'Articles folder is required'}), 400
        if not output_dir:
            return jsonify({'error': 'Output directory is required'}), 400
        if not os.path.exists(articles_folder):
            return jsonify({'error': f'Articles folder not found: {articles_folder}'}), 400
        
        # Import required modules
        from utils.article_llm_analysis.topic_modeling import (
            TopicModelingSystem,
            TopicModelingLevel1,
            TopicModelingLevel2,
            TopicModelingAssign,
            TopicModelingRefine,
            TopicModelingCorrect,
            TOPICGPT_AVAILABLE
        )
        from utils.article_processing.shared_utils import load_config, create_llm, get_use_chat_model
        
        if not TOPICGPT_AVAILABLE:
            return jsonify({'error': 'TopicGPT is required but not installed. Install with: pip install topicgpt_python'}), 400
        
        # Load LLM config
        if not os.path.exists(LLM_CONFIG_PATH):
            return jsonify({'error': f'LLM config file not found: {LLM_CONFIG_PATH}'}), 400
        
        config = load_config(LLM_CONFIG_PATH)
        if provider not in config:
            return jsonify({'error': f'Provider {provider} not found in LLM config'}), 400
        
        provider_config = config[provider]
        
        # Create LLM
        llm = create_llm(provider, provider_config)
        use_chat_model = get_use_chat_model(provider)
        max_output_tokens = provider_config.get("max_output_tokens", 5000)
        context_length = provider_config.get("context_length", 16385)
        temperature = provider_config.get("temperature", 0.7)
        
        # Select step class
        step_classes = {
            'level1': TopicModelingLevel1,
            'level2': TopicModelingLevel2,
            'assign': TopicModelingAssign,
            'refine': TopicModelingRefine,
            'correct': TopicModelingCorrect
        }
        
        step_class = step_classes[step]
        topic_modeling_step = step_class(
            llm,
            use_chat_model,
            context_length,
            max_output_tokens,
            provider,
            provider_config["model"],
            temperature,
        )
        
        topic_modeling_system = TopicModelingSystem(topic_modeling_step)
        
        # Prepare kwargs based on step
        kwargs = {
            'max_workers': 4
        }
        
        if step == 'level1':
            # Get seed_file from step-specific params or fall back to general seed_file
            step_seed_file = data.get('seed_file', seed_file)
            if not step_seed_file:
                return jsonify({'error': 'Seed file is required for level1 step'}), 400
            if not os.path.exists(step_seed_file):
                return jsonify({'error': f'Seed file not found: {step_seed_file}'}), 400
            kwargs['seed_file'] = step_seed_file
            
            # Get output_file from step-specific params
            step_output_file = data.get('output_file', '')
            if step_output_file:
                # Create directory if it doesn't exist
                output_file_dir = os.path.dirname(step_output_file)
                if output_file_dir and not os.path.exists(output_file_dir):
                    os.makedirs(output_file_dir, exist_ok=True)
                kwargs['output_file'] = step_output_file
            
            # Get data_file from step-specific params
            step_data_file = data.get('data_file', '')
            if step_data_file:
                if not os.path.exists(step_data_file):
                    return jsonify({'error': f'Data file not found: {step_data_file}'}), 400
                kwargs['data_file'] = step_data_file
            
            # Get generation_file from step-specific params
            step_generation_file = data.get('generation_file', '')
            if step_generation_file:
                # Create directory if it doesn't exist
                generation_file_dir = os.path.dirname(step_generation_file)
                if generation_file_dir and not os.path.exists(generation_file_dir):
                    os.makedirs(generation_file_dir, exist_ok=True)
                kwargs['generation_file'] = step_generation_file
            
            # Get prompt_file from step-specific params
            step_prompt_file = data.get('prompt_file', '')
            if step_prompt_file:
                if not os.path.exists(step_prompt_file):
                    return jsonify({'error': f'Prompt file not found: {step_prompt_file}'}), 400
                kwargs['prompt_file'] = step_prompt_file
            else:
                # Prompt file is optional - will look in output_dir if not provided
                prompt_file_path = os.path.join(output_dir, 'prompt_lvl1.txt')
                if os.path.exists(prompt_file_path):
                    kwargs['prompt_file'] = prompt_file_path
        elif step == 'level2':
            # Get seed_file from step-specific params or fall back to general seed_file
            step_seed_file = data.get('seed_file', seed_file)
            if not step_seed_file:
                return jsonify({'error': 'Seed file is required for level2 step'}), 400
            if not os.path.exists(step_seed_file):
                return jsonify({'error': f'Seed file not found: {step_seed_file}'}), 400
            kwargs['seed_file'] = step_seed_file
            
            # Get output_file from step-specific params
            step_output_file = data.get('output_file', '')
            if step_output_file:
                # Create directory if it doesn't exist
                output_file_dir = os.path.dirname(step_output_file)
                if output_file_dir and not os.path.exists(output_file_dir):
                    os.makedirs(output_file_dir, exist_ok=True)
                kwargs['output_file'] = step_output_file
            
            # Get data_file from step-specific params
            step_data_file = data.get('data_file', '')
            if step_data_file:
                if not os.path.exists(step_data_file):
                    return jsonify({'error': f'Data file not found: {step_data_file}'}), 400
                kwargs['data_file'] = step_data_file
            
            # Get generation_file from step-specific params
            step_generation_file = data.get('generation_file', '')
            if step_generation_file:
                # Create directory if it doesn't exist
                generation_file_dir = os.path.dirname(step_generation_file)
                if generation_file_dir and not os.path.exists(generation_file_dir):
                    os.makedirs(generation_file_dir, exist_ok=True)
                kwargs['generation_file'] = step_generation_file
            
            # Get prompt_file from step-specific params
            step_prompt_file = data.get('prompt_file', '')
            if step_prompt_file:
                if not os.path.exists(step_prompt_file):
                    return jsonify({'error': f'Prompt file not found: {step_prompt_file}'}), 400
                kwargs['prompt_file'] = step_prompt_file
            else:
                prompt_file_path = os.path.join(output_dir, 'prompt_lvl2.txt')
                if os.path.exists(prompt_file_path):
                    kwargs['prompt_file'] = prompt_file_path
        elif step == 'assign':
            # Get topic_file from step-specific params (optional)
            step_topic_file = data.get('topic_file', '')
            if step_topic_file:
                if not os.path.exists(step_topic_file):
                    return jsonify({'error': f'Topic file not found: {step_topic_file}'}), 400
                kwargs['topic_file'] = step_topic_file
            
            # Get data_file from step-specific params (optional)
            step_data_file = data.get('data_file', '')
            if step_data_file:
                if not os.path.exists(step_data_file):
                    return jsonify({'error': f'Data file not found: {step_data_file}'}), 400
                kwargs['data_file'] = step_data_file
            
            # Get output_file from step-specific params (required)
            step_output_file = data.get('output_file', '')
            if not step_output_file:
                return jsonify({'error': 'Output file is required for assign step'}), 400
            # Create directory if it doesn't exist
            output_file_dir = os.path.dirname(step_output_file)
            if output_file_dir and not os.path.exists(output_file_dir):
                os.makedirs(output_file_dir, exist_ok=True)
            kwargs['output_file'] = step_output_file
            
            # Get prompt_file from step-specific params
            step_prompt_file = data.get('prompt_file', '')
            if step_prompt_file:
                if not os.path.exists(step_prompt_file):
                    return jsonify({'error': f'Prompt file not found: {step_prompt_file}'}), 400
                kwargs['prompt_file'] = step_prompt_file
            else:
                prompt_file_path = os.path.join(output_dir, 'prompt_assign.txt')
                if os.path.exists(prompt_file_path):
                    kwargs['prompt_file'] = prompt_file_path
        elif step == 'refine':
            # Get topic_file from step-specific params
            step_topic_file = data.get('topic_file', '')
            if not step_topic_file:
                return jsonify({'error': 'Topic file is required for refine step'}), 400
            if not os.path.exists(step_topic_file):
                return jsonify({'error': f'Topic file not found: {step_topic_file}'}), 400
            kwargs['topic_file'] = step_topic_file
            
            # Get generation_file from step-specific params
            step_generation_file = data.get('generation_file', '')
            if not step_generation_file:
                return jsonify({'error': 'Generation file is required for refine step'}), 400
            if not os.path.exists(step_generation_file):
                return jsonify({'error': f'Generation file not found: {step_generation_file}'}), 400
            kwargs['generation_file'] = step_generation_file
            
            # Get out_file from step-specific params
            step_out_file = data.get('out_file', '')
            if not step_out_file:
                return jsonify({'error': 'Output file is required for refine step'}), 400
            # Create directory if it doesn't exist
            out_file_dir = os.path.dirname(step_out_file)
            if out_file_dir and not os.path.exists(out_file_dir):
                os.makedirs(out_file_dir, exist_ok=True)
            kwargs['out_file'] = step_out_file
            
            # Get updated_file from step-specific params
            step_updated_file = data.get('updated_file', '')
            if not step_updated_file:
                return jsonify({'error': 'Updated file is required for refine step'}), 400
            # Create directory if it doesn't exist
            updated_file_dir = os.path.dirname(step_updated_file)
            if updated_file_dir and not os.path.exists(updated_file_dir):
                os.makedirs(updated_file_dir, exist_ok=True)
            kwargs['updated_file'] = step_updated_file
            
            # Get prompt_file from step-specific params
            step_prompt_file = data.get('prompt_file', '')
            if step_prompt_file:
                if not os.path.exists(step_prompt_file):
                    return jsonify({'error': f'Prompt file not found: {step_prompt_file}'}), 400
                kwargs['prompt_file'] = step_prompt_file
            else:
                prompt_file_path = os.path.join(output_dir, 'prompt_refine.txt')
                if os.path.exists(prompt_file_path):
                    kwargs['prompt_file'] = prompt_file_path
        elif step == 'correct':
            # Get data_path from step-specific params (required)
            step_data_path = data.get('data_path', '')
            if not step_data_path:
                return jsonify({'error': 'Data path is required for correct step'}), 400
            if not os.path.exists(step_data_path):
                return jsonify({'error': f'Data file not found: {step_data_path}'}), 400
            kwargs['data_path'] = step_data_path
            
            # Get output_path from step-specific params (required)
            step_output_path = data.get('output_path', '')
            if not step_output_path:
                return jsonify({'error': 'Output path is required for correct step'}), 400
            # Create directory if it doesn't exist
            output_path_dir = os.path.dirname(step_output_path)
            if output_path_dir and not os.path.exists(output_path_dir):
                os.makedirs(output_path_dir, exist_ok=True)
            kwargs['output_path'] = step_output_path
            
            # Get topic_path from step-specific params (optional)
            step_topic_path = data.get('topic_path', '')
            if step_topic_path:
                if not os.path.exists(step_topic_path):
                    return jsonify({'error': f'Topic file not found: {step_topic_path}'}), 400
                kwargs['topic_path'] = step_topic_path
            
            # Get prompt_path from step-specific params (optional)
            step_prompt_path = data.get('prompt_path', '')
            if step_prompt_path:
                if not os.path.exists(step_prompt_path):
                    return jsonify({'error': f'Prompt file not found: {step_prompt_path}'}), 400
                kwargs['prompt_path'] = step_prompt_path
            else:
                # Prompt file is optional - will look in output_dir if not provided
                prompt_file_path = os.path.join(output_dir, 'prompt_correct.txt')
                if os.path.exists(prompt_file_path):
                    kwargs['prompt_path'] = prompt_file_path
            
        
        # Execute step
        result = topic_modeling_system.execute_step(
            articles_folder,
            output_dir,
            **kwargs
        )
        
        if result.get("success", False):
            message = f"Step '{step}' completed successfully!"
            if step == 'level1' and 'topics' in result:
                message += f" Generated {len(result['topics'])} topics."
            elif step == 'level2' and 'topics' in result:
                message += f" Generated {len(result['topics'])} topics."
            elif step == 'assign' and 'assignments' in result:
                message += f" Assigned topics to {len(result['assignments'])} documents."
            elif step == 'refine':
                message += " Topics refined successfully."
            elif step == 'correct' and 'assignments' in result:
                message += f" Corrected assignments for {len(result['assignments'])} documents."
            return jsonify({'success': True, 'message': message})
        else:
            error_msg = result.get('error', 'Unknown error')
            return jsonify({'error': error_msg}), 500
        
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@app.route('/api/file/read', methods=['POST'])
def read_file_api():
    """API endpoint to read file contents"""
    try:
        data = request.get_json()
        file_path = data.get('file_path', '').strip()
        
        if not file_path:
            return jsonify({'error': 'File path is required'}), 400
        
        if not os.path.exists(file_path):
            return jsonify({'error': f'File not found: {file_path}'}), 404
        
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        return jsonify({'success': True, 'content': content})
        
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@app.route('/api/file/write', methods=['POST'])
def write_file_api():
    """API endpoint to write file contents"""
    try:
        data = request.get_json()
        file_path = data.get('file_path', '').strip()
        content = data.get('content', '')
        
        if not file_path:
            return jsonify({'error': 'File path is required'}), 400
        
        # Ensure the directory exists
        file_dir = os.path.dirname(file_path)
        if file_dir and not os.path.exists(file_dir):
            os.makedirs(file_dir, exist_ok=True)
        
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        
        return jsonify({'success': True, 'message': f'File saved to {file_path}'})
        
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


if __name__ == '__main__':
    # Create templates and static directories if they don't exist
    os.makedirs('templates', exist_ok=True)
    os.makedirs('static/css', exist_ok=True)
    
    app.run(debug=True, host='0.0.0.0', port=5000)

