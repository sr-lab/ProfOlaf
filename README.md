
# ProfOlaf

![ProfOlaf Logo](banner.webp)

The **ProfOlaf** tool was built to help researchers with literature reviews. It automates the process of snowballing articles through an initial seed, and helps raters through the process of screening.

## Setup - Generating the Search Configuration

**`generate_search_conf.py`** is used to interactively create a `search_conf.json` file that stores all configuration parameters needed for scraping and data collection.

### Features
- Prompts the user for:
  - **Year interval** (`start_year`, `end_year`)
  - **Accepted venue ranks** (comma-separated, e.g. `A, B1, B2`)
  - **Proxy key** or environment variable name (optional)
  - **Initial file** (input seed file)
  - **Path to the database**
  - **Path to the final CSV file**
- Saves all parameters into a JSON file: **`search_conf.json`**
- Provides an easy way to customize and reuse scraping settings.

**Example Usage**

Run the script: `python generate_search_conf.py`

You will be asked step-by-step:
```bash
Enter the starting year: 2020
Enter the ending year: 2025

Enter the accepted venue ranks (stops with empty input): A, B1
Enter the proxy key (or the env variable name): MY_PROXY_KEY

Enter the initial file: seed.txt
Enter the db path: ./data/database.db
Enter the path to the final csv file: ./results/output.csv
Enter the search method used: google scholar
```

**Example Output** (`search_conf.json`)
```json
{
  "start_year": 2020,
  "end_year": 2025,
  "venue_rank_list": ["A", "B1"],
  "proxy_key": "MY_PROXY_KEY",
  "initial_file": "seed.txt",
  "db_path": "./data/database.db",
  "csv_path": " ./results/output.csv",
  "search_method": "google_scholar"
}
```

> [!NOTE]  
> The proxy key is only required when using google scholar as the search method.
>
> Ensure that the initial file, DB path, and CSV path are accessible from your environment

## Step 0 — Generate Snowball Starting Points

**`0_generate_snowball_start.py`** reads paper titles from a file, looks them up on a search database(via the `scholarly` library for google scholar or the semantic scholar api for semantic scholar), and writes the resulting initial publications into your database for iteration 0 of the snowballing process.

### What it does
- Loads config from **`search_conf.json`** (created in Step 1).
- Reads titles from:
  - **JSON:** `{"papers": [{"title": "..."}, ...]}`
  - **TXT:** one title per line
- Queries Google Scholar for each title and builds a normalized record.
- Respects a delay between requests to reduce rate limiting

---

## Accepted input formats

### JSON (preferred)

```json
{
  "papers": [
    { "title": "Awesome Paper Title 1" },
    { "title": "Another Great Title 2" }
  ]
}
```
### TXT

```txt
Awesome Paper Title 1
Another Great Title 2
```
---

### Usage

**With defaults from** `search_conf.json`:
```bash
python 0_generate_snowball_start.py
```
**Override paths and delay:**
```bash
python 0_generate_snowball_start.py \
  --input_file ./data/accepted_papers.json \
  --db_path ./data/database.db \
  --delay 2.5
```
### Arguments

- `--input_file` Path to `.json` or `.txt` with titles (default: `search_conf["initial_file"]`)
- `--db_path` Path to database (default: `search_conf["db_path"]`)
- `--delay` Seconds to sleep between queries (default: `2.0`)

---

### Pipeline context

1. **Step 1:** Generate `search_conf.json` with `generate_search_conf.py`
2. **Step 2 (this step):** `0_generate_snowball_start.py` → seeds iteration 0 in the DB
3. **Next:** Continue with your snowballing/expansion scripts using the stored iteration 0 results


## Step 1 — Snowballing

`1_start_iteration.py` takes the seed publications from the **previous iteration** and fetches the citations (forward snowballing) and references (backward snowballing) for each one.

> [!NOTE]  
> The google scholar search method only supports forward snowballing.
> For both backward and forward snowballing, use semantic scholar as the search_method

---

### What it does

- Loads config from `search_conf.json` (proxy, DB path)
- Opens the database for the target `--iteration`
- Pulls the **seed set from the previous iteration**:  
```python
get_iteration_data(iteration=ITERATION-1, selected=SelectionStage.NOT_SELECTED)
 ```
- For each seed paper, queries ```scholarly.search_citedby(<citedby_id>)```
- Normalizes each result with ```get_article_data(...)``` and writes:
-- ```insert_iteration_data(articles)``` for the current iteration
-- ```insert_seen_titles_data([(title, id), ...])´´´ for deduping
- Uses **exponential backoff** (starts at 30s) on failures to reduce rate limiting
- If a paper has no ```citedby_url```, falls back to a **SHA-256 hash of the title** as its ID

---

### Usage

**Typical: expand from iteration 0 → 1**

```bash
python 1_start_iteration.py --iteration 1
```

**Custom DB path**
```bash
python 1_start_iteration.py --iteration 2 --db_path ./data/database.db
```

**Arguments**
- `--iteration` Target iteration to generate (int). Seeds are read from `iteration-1`
- `--db_path` Path to the SQLite DB (default: `search_conf["db_path"]`)

---

### Input / Output

**Input**
- DB must already contain **iteration N-1** data (e.g., created by `0_generate_snowball_start.py` for iteration 0)

**Writes to DB**
- Current iteration’s articles (normalized records)
- `seen_titles` pairs `(title, id)` used for deduplication

---

### Troubleshooting

- **“No citations found”**: The seed’s `citedby` page has zero results—this is normal for some papers.
- **Captcha / throttling**: Ensure a working proxy and let the backoff run; rerun later if needed.
- **Seed count is zero**: Verify that the previous iteration exists in the DB and that items are marked with `SelectionStage.NOT_SELECTED`.

---

## Step 2 — Fetch BibTeX for Iteration *N*

`2_get_bibtex.py` enriches the papers in **iteration N** by fetching their **BibTeX** from Google Scholar (via `scholarly`) and updating your database. The information present in the bibtex is necessary for the metadata screening step

---

### What it does

- Loads config from `search_conf.json` (proxy, DB path)
- Reads all articles for the target iteration from the DB
- For each article:
  1. Looks up the publication by **title** (`scholarly.search_single_pub` → `scholarly.bibtex`)
  2. Parses the BibTeX to extract the **venue** (`booktitle` or `journal`)
  3. If the venue looks like **arXiv/CoRR**, it tries to find a **non-arXiv version** by checking all versions (`scholarly.get_all_versions`) and selecting one with a proper venue (conference/journal)
  4. Writes the chosen BibTeX back to the DB (`update_iteration_data`)
- Uses **exponential backoff** (starting at 30s) on errors to reduce throttling

---

### Usage

Fetch BibTeX for **iteration 1:**

```bash
python 2_get_bibtex.py --iteration 1
```

Custom DB path:
```bash
python 2_get_bibtex.py --iteration 1 --db_path ./data/database.db
```

**Arguments**
- `--iteration` *(required)* Target iteration number (int) 
- `--db_path` *(optional)* Path to the SQLite DB (default: `search_conf["db_path"]`)

---

### Input / Output

**Input**
- DB entries for **iteration N** (e.g., produced by `1_start_iteration.py`)

**Writes to DB**
- Updates each article in iteration N with a `bibtex` string

---

### Proxy & Rate Limiting

- Proxy session is initialized via `get_proxy(search_conf["proxy_key"])`
- Google Scholar may throttle; the script retries with **exponential backoff** (30s → 60s → 120s ...)

---

### Troubleshooting

- **Repeated retries / never finishes on arXiv-only papers**  
  The script is strict about replacing arXiv/CoRR with a non-arXiv venue and will keep trying 
  Consider relaxing this logic if arXiv should be accepted

- **Captcha / throttling**  
  Use a reliable proxy; give the backoff time to proceed; rerun later if needed

- **Venue not detected**  
  The venue is extracted from `booktitle` or `journal`. Some BibTeX records lack these fields; alternative versions are attempted

---

## Step 3 — Assign Venue Ranks (interactive)

`3_generate_conf_rank.py` scans the **iteration N** articles’ BibTeX, extracts their venues (conference/journal), and lets you **assign a rank** to any venue that isn’t already in your DB. Results are written to the `conf_rank` table as you go.

To assist this manual process, the tool searches both Scimago and a local core ranking database foe the venues.

> [!NOTE] 
> Run Step 4 (`2_get_bibtex.py`) first so venues can be read from BibTeX.

---

### Usage

Rank venues for **iteration 1**:
```bash
python 3_generate_conf_rank.py --iteration 1
```

Custom DB path:
```bash
python 3_generate_conf_rank.py --iteration 1 --db_path ./data/database.db
```

### Arguments
- `--iteration` *(required)* Target iteration number (int)
- `--db_path` *(optional)* Path to the SQLite DB (default: from `search_conf.json`)

---

### Example session

```kotlin
(1/5) IEEE Symposium on Example Security
What is the rank of this venue? A

(2/5) Journal of Hypothetical Research
What is the rank of this venue? Q1

(3/5) arXiv
-> auto-assigned NA
...
```

Each answer is **immediately stored**:

- `db_manager.insert_conf_rank_data([(venue, rank)])`

---

### Input / Output

**Input**
- DB entries for **iteration N**, each with a **BibTeX** string (from Step 4)

**Writes to DB**
- Table with venue–rank pairs (queried via `db_manager.get_conf_rank_data()`)

---

### Troubleshooting

- **No venues found** → Ensure Step 4 populated BibTeX for this iteration
- **Invalid rank** → The script will reprompt until you enter a valid label
- **arXiv/SSRN assigned as NA** → This is by design; override later by updating the DB if you need a different policy

---

## Step 4 — Filter by Metadata 

`4_filter_by_metadata.py` reviews **iteration N** records and decides whether each paper is **selected** or **filtered out** based on venue/peer-review, year window, language, and download availability. It writes the results back to the DB in a single batch.

### What it checks (in order)

1. **Venue & peer-review**
   - Parses the article’s **BibTeX** and extracts `booktitle` or `journal`
   - Automatically rejects if the BibTeX `ENTRYTYPE` is `book`, `phdthesis`, or `mastersthesis`, or if venue is `NA`/missing
   - Looks up the venue’s rank in the DB and compares it against `search_conf["venue_rank_list"]`
   - If the venue isn’t known in the DB, it asks you: `Is the publication peer-reviewed and A or B or ... (y/n)`

2. **Year window**
   - Accepts if `pub_year` is between `search_conf["start_year"]` and `search_conf["end_year"]`
   - If the year is unknown/non-numeric, it asks you to confirm

3. **Language (English)**
   - If the venue check already passed (peer-reviewed + ranked OK), it auto-assumes English
   - Otherwise, it asks: `Is the publication in English (y/n)`

4. **Download availability**
   - Accepts if an `eprint_url` is present; else asks: `Is the publication available for download (y/n)`

If all checks pass → **Selected**. Otherwise the first failing reason is recorded.

---

### DB writes

For each article, one of the following fields is updated (via `update_batch_iteration_data`):

| Outcome                   | Field set on the article                 |
|---------------------------|------------------------------------------|
| Venue/peer-review failed  | `venue_filtered_out = True`              |
| Year outside window       | `year_filtered_out = True`               |
| Not English               | `language_filtered_out = True`           |
| No downloadable copy      | `download_filtered_out = True`           |
| All checks passed         | `selected = SelectionStage.SELECTED`     |

---

### Usage

**Filter iteration 1:**
```bash
python 4_filter_by_metadata.py --iteration 1
```
Custom DB path:
```bash
python 4_filter_by_metadata.py --iteration 1 --db_path ./data/database.db
```

### Arguments
- `--iteration` *(required)* Target iteration (int)
- `--db_path` *(optional)* SQLite DB path (default: from `search_conf.json`)

---

### Example session

```pgsql
Element 3 out of 42
ID: 123456
Title: Cool Paper on X
Venue: IEEE S&P
Url: https://example.org/paper.pdf

Is the publication peer-reviewed and A or B or Q1 (y/n): y
Is the publication year between 2018 and 2024 (y/n): y

Selected
```

---

> [!NOTE]
> **Auto-logic shortcut:** If venue + rank already prove peer-review and the venue is in your allowed list (`venue_rank_list`), `check_english` returns `True` without aski_
>
>**Unknown year:** You’re prompted to confirm it’s within the configured window
>
>**Interactive prompts:** The script is designed to be conservative—if metadata is incomplete, it asks you rather than guessing
--- 

## Step 5 — Title Screening

`5_filter_by_title.py` iteratively asks the user  if he wants to keep each paper or not, based solely on the title. Along with the user's choice (yes, no, or skip), ProfOlaf also prompts the user for its reasoning in their choice. This is used to help each rater remember their thought process in the following steps
### Usage

**Filter iteration 1:**
```bash
python 5_filter_by_title.py --iteration 1
```
Custom DB path:
```bash
python 5_filter_by_title.py  --iteration 1 --db_path ./data/database.db
```

### Arguments
- `--iteration` *(required)* Target iteration (int)
- `--db_path` *(optional)* SQLite DB path (default: from `search_conf.json`)

---

## Step 6 — Solve Title Screening Disagreements

`6_8_solve_disagreements.py` is used to collect the search databases of different users/raters and reach a consensus on the articles where there was a decision conflict. The script goes over the articles where at least 2 users disagreed in their screening process and presents the reasoning of each rater. After a careful discussion, the raters can make their final decision on the relevance of the paper.

This script is used twice: after **Step 5** and **Step 7**.

### Usage

**Filter iteration 1:**
```bash
python 6_8_solve_disagreements.py --iteration 1 --search_dbs rater1.db rater2.db ... --selection_stage TITLE
```

### Arguments
- `--iteration` *(required)* Target iteration (int)
- `--search_dbs` *(required)* SQLite DB paths (one for each rater involved)
- `--selection_stage` *(required: TITLE or CONTENT)* String representing which disagreements are being processed 
---

## Step 7 — Full Content Screening

`7_filter_by_content.py` iteratively asks the user if he wants to keep each paper or not, based on the content of the full paper. For each article, the tool presents the url for the article. Along with the user's choice (yes, no, or skip), ProfOlaf also prompts the user for its reasoning in their choice. This is used to help each rater remember their thought process in the following steps
### Usage

**Filter iteration 1:**
```bash
python 5_filter_by_title.py --iteration 1
```
Custom DB path:
```bash
python 5_filter_by_title.py  --iteration 1 --db_path ./data/database.db
```

### Arguments
- `--iteration` *(required)* Target iteration (int)
- `--db_path` *(optional)* SQLite DB path (default: from `search_conf.json`)

---

## Step 8 — Solve Content Screening Disagreements

`6_8_solve_disagreements.py` is used again, similarly to the previous steps

```
### Usage

```bash
python 6_8_solve_disagreements.py --iteration 1 --search_dbs rater1.db rater2.db ... --selection_stage CONTENT
```

### Arguments
- `--iteration` *(required)* Target iteration (int)
- `--search_dbs` *(required)* SQLite DB paths (one for each rater involved)
- `--selection_stage` *(required: TITLE or CONTENT)* String representing which disagreements are being processed 
---

## Step 9 — Remove Duplicates

`9_remove_duplicates.py` is used to remove potential duplicates in the results. The same article is often present in a certain database under a slightly different title. This script catches possible duplicate articles in the database and prompts the user which one he wants to keep (or if it is a false positive and both should be kept).


### Usage

```bash
python 9_remove_duplicates.py --iterations 1 2 3 4
```

### Arguments
- `--iterations` *(required)* Iterations to include
- `--db_path` *(optional)* Final SQLite DB path
- `--auto-remove` *(optional)* Automatically removes duplicates without user confirmation 
---

## Step 10 — Generate CSV

`10_generate_csv.py` takes the information on the final search database and exports it into a csv

### Usage

```bash
python 10_generate_csv.py --iterations 1 2 3 4 
```

### Arguments
- `--iterations` *(required)* Iterations to include
- `--db_path` *(optional)* Final SQLite DB path
- `--output_path` *(optional)* Automatically removes duplicates without user confirmation 
---









  




