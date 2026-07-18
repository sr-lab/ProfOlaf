# ProfOlaf

![ProfOlaf Logo](banner.webp)

**ProfOlaf** was built to help researchers with literature reviews. It automates the process of snowballing articles through an initial seed, and helps raters through the process of screening. It also provides several LLM-assisted tools for article analysis.

**ProfOlaf** is described in our [FSE'26 paper](https://arxiv.org/pdf/2510.26750).

If you use our tool, please cite it:
```
@inproceedings{afonso2025profolaf,
author = {Afonso, Martim and Saavedra, Nuno and Louren\c{c}o, Bruno and Mendes, Alexandra and Ferreira, Jo\~{a}o F.},
title = {{ProfOlaf}: Semi-Automated Tool for Systematic Literature Reviews},
year = {2026},
isbn = {9798400726361},
publisher = {Association for Computing Machinery},
address = {New York, NY, USA},
url = {https://doi.org/10.1145/3803437.3806432},
doi = {10.1145/3803437.3806432},
booktitle = {Proceedings of the 34th ACM International Conference on the Foundations of Software Engineering},
pages = {262–266},
numpages = {5},
keywords = {systematic literature reviews, automation, LLM},
location = {Concordia University, Montreal, QC, Canada},
series = {FSE Companion '26}
}
```

## Prerequisites and Input

Currently, ProfOlaf can only be ran with Python 3.10. Other versions might cause dependency conflicts. Please create a virtual environment with Python 3.10 to run the tool.

Before running ProfOlaf, the user must prepare:

- **Seed file**: A plain-text (`.txt`) or JSON file containing the titles of the seed articles. These articles represent the starting point of the snowballing process.
- **Python environment**: Install dependencies with `pip install -r requirements.txt`.
- **LLM configuration** (optional): For LLM-assisted screening and topic modeling, create `utils/article_llm_analysis/llm_config.json` with API keys for OpenAI, Gemini, or Anthropic.

## Main Snowballing Pipeline

The snowballing workflow consists of the following steps, executed sequentially:

### `utils/setup/generate_search_conf.py`

Generates the search configuration used to query the supported scholarly databases. It stores metadata filtering criteria, file paths, and search method in `confs/search_conf.json`. Run interactively—no arguments required.

**Prompts for:**
- Year interval (start/end)
- Venue rank list (A*, A, B, C, D, Q1–Q4, NA)
- Proxy key (or env variable name) for web scraping
- Initial seed file path
- Database path
- Output CSV path
- Search method: `google_scholar`, `semantic_scholar`, or `dblp`
- Annotations (inclusion criteria)

### `0_generate_snowball_start.py`

Initializes the snowballing process using the seed file and stores the initial set of articles in the database.

| Argument | Description | Default |
|----------|-------------|---------|
| `--input_file` | Path to seed file (txt or json) | `search_conf["initial_file"]` |
| `--delay` | Delay between API requests (seconds) | `1.0` |
| `--db_path` | Database path | `search_conf["db_path"]` |
| `--search_method` | `google_scholar`, `semantic_scholar`, or `dblp` | `search_conf["search_method"]` |

### `1_start_iteration.py`

Starts a new snowballing iteration, collecting forward and backward citations from the set of articles of the previous iteration (or the initial set when starting a new search). Currently, only **Semantic Scholar** provides both backward and forward snowballing; Google Scholar provides citations only. The CLI script also supports repairing broken or unidentified references.

| Argument | Description | Default |
|----------|-------------|---------|
| `--iteration` | Iteration number | *required* |
| `--db_path` | Database path | `search_conf["db_path"]` |
| `--search_method` | `google_scholar`, `semantic_scholar`, or `dblp` | `search_conf["search_method"]` |
| `--repair` | Repair method for broken references: `remove` or `manual` | *required* |
| `--verbose` | Verbose output | flag |

**Note:** The full citation collection is available through the web application (`app.py`). The CLI script focuses on reference repair.

### `2_remove_duplicates.py`

Identifies and removes duplicate entries across databases based on title similarity.

| Argument | Description | Default |
|----------|-------------|---------|
| `--db_path` | Database path | `search_conf["db_path"]` |
| `--iterations` | Iteration numbers to process (e.g. `1 2 3`) | — |
| `--similarity_threshold` | Title similarity threshold (0.0–1.0) | `0.8` |
| `--auto_remove` | Remove duplicates without confirmation | flag |

### `3_get_bibtex.py`

Retrieves BibTeX metadata for the collected articles. Without a web-scraping proxy, **Semantic Scholar** is recommended. Too many requests to Google Scholar may result in a block.

| Argument | Description | Default |
|----------|-------------|---------|
| `--iteration` | Iteration number | — |
| `--db_path` | Database path | `search_conf["db_path"]` |
| `--batch_size` | Batch size for processing | `1` |
| `--max_workers` | Max parallel workers | `3` |
| `--parallel` | Disable parallel processing | flag |
| `--delay` | Delay between requests (seconds) | `1.0` |
| `--search_method` | Search method | `search_conf["search_method"]` |

### (Optional) `4_generate_conf_rank.py`

Filters articles based on venue ranking if you wish to restrict the corpus to specific publication venues. Uses the venue rank list from `search_conf.json` and fetches ranks from CORE and Scimago when needed.

| Argument | Description | Default |
|----------|-------------|---------|
| `--iteration` | Iteration number | — |
| `--db_path` | Database path | `search_conf["db_path"]` |

### `5_filter_by_metadata.py`

Filters articles according to metadata attributes (year, venue, online availability, language). Configurable via `search_conf.json`.

| Argument | Description | Default |
|----------|-------------|---------|
| `--iteration` | Iteration number | *required* |
| `--db_path` | Database path | `search_conf["db_path"]` |
| `--disable_venue_check` | Skip venue check | flag |
| `--disable_year_check` | Skip year check | flag |
| `--disable_english_check` | Skip language check | flag |
| `--disable_download_check` | Skip download availability check | flag |

### `6_filter_by_title.py`

Performs title-based screening. The user is interactively prompted to decide whether to keep or discard each article, with a brief justification. Users are encouraged to be conservative and only discard clearly irrelevant articles. Optionally, LLM-based screening can be enabled.

| Argument | Description | Default |
|----------|-------------|---------|
| `--iteration` | Iteration number | *required* |
| `--db-path` | Database path | `search_conf["db_path"]` |
| `--rater` | Rater name or ID | *required* |
| `--llm` | Use LLM for screening | `False` |
| `--model` | LLM model (e.g. `gpt-4o`) | `gpt-4o` |
| `--api-key` | API key for LLM | — |

### `7_solve_title_disagreements.py`

Resolves disagreements between multiple raters. Presents articles for which raters disagreed, along with their reasoning, and prompts them to reach a consensus decision.

| Argument | Description |
|----------|-------------|
| `--iteration` | Iteration number |
| `--search_dbs` | One or more database paths (e.g. `db1.db db2.db`) |

### `8_filter_by_content.py`

Performs content-based screening using the full text (abstract and introduction) of the articles, following the same interaction model as title-based filtering.

| Argument | Description | Default |
|----------|-------------|---------|
| `--iteration` | Iteration number | *required* |
| `--db-path` | Database path | `search_conf["db_path"]` |
| `--rater` | Rater ID | *required* |
| `--llm` | Use LLM for screening | `False` |
| `--model` | LLM model | `gpt-4o` |
| `--api-key` | API key for LLM | — |
| `--article_folder` | Folder to store downloaded PDFs (required for LLM content screening) | — |

### `9_solve_content_disagreements.py`

Resolves rater disagreements from content-based screening. The resulting set after this step marks the end of an iteration.

| Argument | Description |
|----------|-------------|
| `--iteration` | Iteration number |
| `--search_dbs` | One or more database paths |

### Iteration

Steps 1 through 9 are repeated until no new articles are discovered.

### `10_generate_csv.py`

Produces the final CSV file containing the selected articles and their metadata.

| Argument | Description | Default |
|----------|-------------|---------|
| `--iterations` | Iteration numbers to include (e.g. `1 2 3`) | — |
| `--db_path` | Database path | `search_conf["db_path"]` |
| `--output_path` | Output CSV path | `search_conf["csv_path"]` |

---

## Additional Analysis Scripts

For post hoc analysis of the final article set, run:

### `utils/article_llm_analysis/generate_llm_analysis_conf.py`

The analysis configuration is typically created via the web application, which writes `confs/analysis_conf.json` with:

- `articles_folder`: Path to store downloaded PDFs
- `csv_path`: Path to the final CSV
- `seed_file`: Seed file for topic modeling
- `output_path`: Output directory for topic modeling results
- `topics_file`: Topics file name
- `llm_config`: Path to LLM configuration

For CLI-only usage, create `confs/analysis_conf.json` manually with these keys.

### `11_download_pdfs.py`

Downloads all article PDFs to a folder for subsequent analysis.

| Argument | Description | Default |
|----------|-------------|---------|
| `--csv_file` | Input CSV with article metadata | `search_conf["csv_path"]` |
| `--article_folder` | Output folder for PDFs | `analysis_conf["articles_folder"]` |

**Note:** Requires `confs/search_conf.json` and `confs/analysis_conf.json`.

### Topic Modeling

Topic modeling is run through five scripts using TopicGPT with LangChain LLM support. All scripts expect `confs/analysis_conf.json` and an LLM config (e.g. `utils/article_llm_analysis/llm_config.json`). Use `--help-detailed` on any topic modeling script for full documentation.

#### `11_topic_modeling_lvl1.py`

Generates high-level topics from the article set. Extracts text from PDFs automatically.

| Argument | Description | Default |
|----------|-------------|---------|
| `pdf_folder` | Folder containing PDFs | — |
| `--output-dir` | Output directory | `analysis_conf["output_path"]` |
| `--config` | LLM config file | `paper_analysis/llm_config.json` |
| `--provider` | `openai`, `gemini`, `anthropic` | `openai` |
| `--seed-file` | Seed file for topic generation | `analysis_conf["output_path"]/analysis_conf["seed_file"]` |
| `--prompt-file` | Custom prompt file | — |
| `--max-workers` | Parallel workers | `4` |

#### `11_topic_modeling_lvl2.py`

Generates more specific sub-topics from the level-1 topics.

| Argument | Description | Default |
|----------|-------------|---------|
| `pdf_folder` | Folder containing PDFs | — |
| `--output-dir` | Output directory | `analysis_conf["output_path"]` |
| `--seed-file` | Level-1 topics file (e.g. `topics_lvl1.md`) | `output_dir/topics_lvl1.md` |
| `--prompt-file` | Custom prompt for level 2 | — |
| `--config`, `--provider`, `--max-workers` | Same as level 1 | — |

#### `11_topic_modeling_refine.py`

Merges similar topics and removes overly specific or redundant topics (e.g. those in &lt;1% of articles).

| Argument | Description | Default |
|----------|-------------|---------|
| `pdf_folder` | Folder containing PDFs | — |
| `--topic-file` | Topic file to refine | *required* |
| `--generation-file` | Generation JSON file | *required* |
| `--out-file` | Refined topics output | `output_dir/topics_refined.md` |
| `--updated-file` | Updated generation JSON | `output_dir/refinement.json` |
| `--no-remove` | Do not remove topics during refinement | flag |
| `--prompt-file` | Custom refinement prompt | — |

#### `11_topic_modeling_assign.py`

Assigns the generated topics to each article.

| Argument | Description | Default |
|----------|-------------|---------|
| `pdf_folder` | Folder containing PDFs | — |
| `--topic-file` | Topic file (auto-detected if omitted) | — |
| `--prompt-file` | Assignment prompt | `utils/prompts/topic_modeling_prompts/assignment.txt` |
| `--data-file` | Data file (e.g. `data.jsonl`) | `output_dir/data.jsonl` |
| `--output-file` | Assignments output | `output_dir/assignments.jsonl` |

#### `11_topic_modeling_correct.py`

Corrects hallucinated or erroneous topic assignments.

| Argument | Description | Default |
|----------|-------------|---------|
| `pdf_folder` | Folder containing PDFs | — |
| `--data-path` | Assignments file (e.g. `assignments.jsonl`) | `output_dir/assignments.jsonl` |
| `--output-path` | Corrected assignments output | `output_dir/corrected_assignments.jsonl` |
| `--topic-path` | Topic file | auto-detected |
| `--prompt-path` | Correction prompt | — |

### Task Assistant

The task assistant module is run using `11_task_assistant.py`. Add prompt files (`.txt`) under a folder specified in the analysis configuration; the script runs the LLM on each article for each prompt.

| Argument | Description | Default |
|----------|-------------|---------|
| `prompts_folder` | Folder containing `.txt` prompt files | *required* |
| `--config` | LLM config file | `paper_analysis/llm_config.json` |
| `--provider` | `openai`, `gemini`, `anthropic`, `openai-completion` | `openai` |
| `--output` | Output file for results (JSON) | — |
| `--pdf-folder` | Folder containing PDFs | — |
| `--single-pdf` | Process a single PDF | — |
| `--max-workers` | Parallel workers | `1` |

---

## Web Application

Run the web application with:

```bash
python app.py
```

The app is available at `http://localhost:5000` and provides a graphical interface for the entire pipeline, including snowball start generation, iteration collection, BibTeX retrieval, screening, and analysis configuration.

## Docker

Build and run with Docker:

```bash
docker build -t profolaf .
docker run -p 5000:5000 profolaf
```

On startup, choose between the web application (port 5000) or an interactive shell for running CLI scripts.

## Replication Package

Our replication package with all our experiments is available in [zenodo](https://zenodo.org/records/18399259). Please do not forget to git pull to use the latest version of the tool.

---

This document provides a walkthrough of ProfOlaf, demonstrating how the tool supports automated and semi-automated snowballing for literature reviews. The tool is available both as a **web application** and as a **command-line interface**. Here we describe the typical usage of the command-line version, which exposes the full pipeline.