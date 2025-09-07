The **ProfOlaf** tool was built to help researchers with literature **snowballing**. It lets you define a reusable search configuration (time window, source/venue filters, paths, optional proxy). It ingests seed titles from structured or plain-text inputs, queries scholarly sources, and normalizes records.
It stores initial results and “seen” items in your data store, with progress reporting and request throttling to minimize rate limits.

## Step 1 - Generating the Search Configuration

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
```

**Example Output** (`search_conf.json`)
```json
{
  "start_year": 2020,
  "end_year": 2025,
  "venue_rank_list": ["A", "B1"],
  "proxy_key": "MY_PROXY_KEY",
  "initial_file": "seed.txt",
  "db_path": "./data/database.db"
}
```
⚠️ **Note:**
- The proxy key is optional (you can skip it if not required)
- Ensure that the initial file, DB path, and CSV path are accessible from your environment

## Step 2 — Generate Snowball Starting Points

**`0_generate_snowball_start.py`** reads paper titles from a file, looks them up on Google Scholar (via the `scholarly` library), and writes the resulting initial publications and seen titles into your database for iteration 0 of the snowballing process.

### What it does
- Loads config from **`search_conf.json`** (created in Step 1).
- Reads titles from:
  - **JSON:** `{"papers": [{"title": "..."}, ...]}`
  - **TXT:** one title per line
- Queries Google Scholar for each title and builds a normalized record.
- Inserts results into the DB using `utils.db_management`:
  - `insert_iteration_data(initial_pubs)`
  - `insert_seen_titles_data(seen_titles)`
- Respects a delay between requests to reduce rate limiting.

ℹ️ If Google Scholar doesn’t return a cited-by URL/ID, the script still stores the paper using an **MD5 of the title** as a fallback identifier.

---

### Requirements
- Python **3.8+**
- Packages: `scholarly`, `tqdm`, `requests`, `python-dotenv`
- Local modules:
  - `utils.proxy_generator.get_proxy`
  - `utils.db_management` (`DBManager`, `get_article_data`, `initialize_db`, `SelectionStage`)
- Config file: **`search_conf.json`** (from Step 1) with:
  ```json
  {
    "start_year": 2020,
    "end_year": 2024,
    "venue_rank_list": ["A", "B1"],
    "proxy_key": "MY_PROXY_KEY_OR_ENV_NAME",
    "initial_file": "accepted_papers.json or seed_titles.txt",
    "db_path": "./data/database.db"
  }

- (Optional) `.env` if your `proxy_key` refers to an environment variable.

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

### Output / Side effects

- Inserts **iteration 0** publications into the DB
- Tracks **seen titles** as `(title, id)` pairs (ID may be Google Scholar’s cited-by ID or a hash fallback)
- Progress bar shown via `tqdm`

---

### Proxy & rate limiting

- Proxy is resolved via `utils.proxy_generator.get_proxy(search_conf["proxy_key"])`
- Use a working proxy and keep a **non-zero delay** to avoid blocking

---

### Troubleshooting

- **“Unsupported file type”** → Use `.json` with the `"papers"` format or a `.txt` with one title per line  
- **No results for a title** → The script continues; that title won’t be added 
- **Rate limited / Captcha** → Increase `--delay`, verify proxy, or rotate proxies  
- **Env var proxy** → Put it in `.env` (loaded by `python-dotenv`) or export it in your shell

---

### Pipeline context

1. **Step 1:** Generate `search_conf.json` with `generate_search_conf.py`
2. **Step 2 (this step):** `0_generate_snowball_start.py` → seeds iteration 0 in the DB
3. **Next:** Continue with your snowballing/expansion scripts using the stored iteration 0 results


## Step 3 — Expand Citations for Iteration *N*

`1_start_iteration.py` takes the seed publications from the **previous iteration** and expands them by fetching their **citing papers** from Google Scholar (via `scholarly`). The new papers are stored in the DB as the results of the current iteration.

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

**Notes**

- Seeds without a numeric citedby ID are skipped
- Titles not present in ```seen_titles``` (per ```db_manager.get_seen_title(...)```) are skipped by this script
  
---

### Requirements

- Python 3.8+
- Packages: `scholarly`, `python-dotenv`
- Local modules:  
  - `utils.proxy_generator.get_proxy`  
  - `utils.db_management` (`DBManager`, `get_article_data`, `initialize_db`, `SelectionStage`)
- Config: `search_conf.json` created in Step 1  
  (must include `proxy_key` and `db_path`)

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

### Proxy & Rate Limiting

- Proxy is resolved via `get_proxy(search_conf["proxy_key"])` (supports env-based keys; `.env` loaded by `python-dotenv`).
- Google Scholar may throttle; the script retries with **exponential backoff** (30s → 60s → 120s ...).

---

### Troubleshooting

- **“No citations found”**: The seed’s `citedby` page has zero results—this is normal for some papers.
- **Captcha / throttling**: Ensure a working proxy and let the backoff run; rerun later if needed.
- **Seed count is zero**: Verify that the previous iteration exists in the DB and that items are marked with `SelectionStage.NOT_SELECTED`.

---

### Pipeline context

1. **Step 1:** Create `search_conf.json` with `generate_search_conf.py`  
2. **Step 2:** Seed iteration 0 with `0_generate_snowball_start.py`  
3. **Step 3 (this script):** `1_start_iteration.py` → expand citations for iteration *N* using seeds from *N-1*  
4. **Next:** Repeat for subsequent iterations or run your filtering/selection stages

## Step 4 — Fetch BibTeX for Iteration *N*

`2_get_bibtex.py` enriches the papers in **iteration N** by fetching their **BibTeX** from Google Scholar (via `scholarly`) and updating your database.

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

### Notes

- Books and theses are ignored for venue extraction (BibTeX entry types: `book`, `phdthesis`, `mastersthesis`).  
- If a non-arXiv venue isn’t found, the script keeps retrying until it does (by design). You may wish to relax this if your corpus legitimately contains arXiv-only entries.

---

### Requirements

- Python 3.8+
- Packages: `scholarly`, `python-dotenv`, `bibtexparser`
- Local modules:  
  - `utils.proxy_generator.get_proxy`  
  - `utils.db_management` (`DBManager`, `ArticleData`, `get_article_data`, `initialize_db`)
- Config: `search_conf.json` created in Step 1 (must include `proxy_key` and `db_path`)
- (Optional) `.env` if your proxy key is stored as an env var

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

### Pipeline context

1. **Step 1:** Create `search_conf.json` with `generate_search_conf.py`  
2. **Step 2:** Seed iteration 0 with `0_generate_snowball_start.py`  
3. **Step 3:** Expand citations for iteration *N* with `1_start_iteration.py`  
4. **Step 4 (this script):** `2_get_bibtex.py` — attach BibTeX metadata to papers in iteration *N*

---

### Implementation footnote (for maintainers)

- `get_bibtex_venue(bibtex: str)` is intended to parse the **passed BibTeX string** with `bibtexparser.loads(bibtex)` and read `booktitle` / `journal`. If you see a reference to `article.bibtex` inside that function, adjust it to use the `bibtex` argument.

