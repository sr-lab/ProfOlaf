import sqlite3
import json
import os
from collections import defaultdict
from dataclasses import dataclass, asdict, fields
from typing import List, Tuple
from enum import Enum

# Enum for the different selection stages of the process
class SelectionStage(Enum):
    DUPLICATE = -1
    NOT_SELECTED = 0
    METADATA_APPROVED = 1
    TITLE_APPROVED = 2
    CONTENT_APPROVED = 3
    
@dataclass
class ArticleData:
    id: str = ""
    container_type: str = ""
    source: str = ""
    title: str = ""
    authors: str = ""
    venue: str = ""
    pub_year: int = 0
    pub_url: str = ""
    num_citations: int = -1
    citedby_url: str = ""
    url_related_articles: str = ""
    eprint_url: str = ""
    download_filtered_out: bool = None  
    language_filtered_out: bool = None
    venue_filtered_out: bool = None
    year_filtered_out: bool = None
    keep_title: bool = None  # Replaces title_filtered_out - True means keep, False means filtered out
    keep_content: bool = None  # Replaces abstract_filtered_out - True means keep, False means filtered out
    new_pub: bool = None
    selected: int = 0
    bibtex: str = ""
    iteration: int = 0
    duplicate: bool = False
    search_method: str = ""
    doi: str = ""
    abstract: str = ""
    dict = asdict

    def set_iteration(self, iteration: int):
        self.iteration = iteration
    def set_selected(self, selected: SelectionStage):
        self.selected = selected
    def set_bibtex(self, bibtex: str):
        self.bibtex = bibtex
    def set_duplicate(self, duplicate: bool):
        self.duplicate = duplicate
    def set_search_method(self, search_method: str):
        self.search_method = search_method
    def __hash__(self):
        # Use id as the primary hash since it should be unique
        return hash(self.id)
    
    def __eq__(self, other):
        if not isinstance(other, ArticleData):
            return False
        return self.id == other.id

def get_article_data(pub, pub_id, iteration: int = 0, selected: SelectionStage = SelectionStage.NOT_SELECTED, new_pub: bool = False, search_method: str = ""):
    """
    Get the article data from the pub.
    """
    pub_info = {}
    pub_info["id"] = pub_id
    pub_info["container_type"] = pub.get("container_type", "")
    pub_info["eprint_url"] = pub.get("pub_url", "") if "eprint_url" not in pub else pub["eprint_url"]
    pub_info["source"] = pub.get("source", "")
    pub_info["title"] = pub.get("bib", {}).get("title", "")
    pub_info["authors"] = pub.get("bib", {}).get("author", "")
    pub_info["venue"] = pub.get("bib", {}).get("venue", "")
    pub_info["pub_year"] = "0" if not pub.get("bib", {}).get("pub_year", "").isdigit() else pub.get("bib", {}).get("pub_year", "")
    pub_info["pub_url"] = pub.get("pub_url", "")
    pub_info["num_citations"] = pub.get("num_citations", 0)
    pub_info["citedby_url"] = pub.get("citedby_url", "")
    pub_info["url_related_articles"] = pub.get("url_related_articles", "")
    pub_info["new_pub"] = new_pub
    pub_info["selected"] = selected
    pub_info["iteration"] = iteration
    pub_info["search_method"] = search_method
    return ArticleData(**pub_info)


def build_citation_edge(seed, discovered, direction: str, iteration: int) -> Tuple:
    """Convert a snowball result into a *canonical* directed citation edge.

    ``direction`` describes how ``discovered`` relates to ``seed``:
      - ``'citation'``  : ``discovered`` cites ``seed``  -> edge ``discovered -> seed``
      - ``'reference'`` : ``seed`` cites ``discovered`` -> edge ``seed -> discovered``

    A citation edge is always oriented *citing -> cited*, so readers never need to
    reinterpret a provenance ``direction``. Returns
    ``(citing_id, cited_id, citing_doi, cited_doi, source, iteration)``.
    """
    if direction == "citation":
        citing, cited = discovered, seed
    else:  # 'reference'
        citing, cited = seed, discovered
    return (
        str(getattr(citing, "id", "") or ""),
        str(getattr(cited, "id", "") or ""),
        str(getattr(citing, "doi", "") or ""),
        str(getattr(cited, "doi", "") or ""),
        str(getattr(discovered, "search_method", "") or ""),
        int(iteration),
    )


class DBManager:
    SQL_TYPES = {
        str: 'TEXT',
        int: 'TEXT',  # Changed from INTEGER to TEXT to handle large integers
        float: 'REAL',
        bool: 'BOOLEAN'
    }
    def __init__(self, db_path: str, new_db: bool = False):
        if not new_db and not os.path.exists(db_path):
            raise ValueError(f"Database file does not exist: {db_path}")
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.cursor = self.conn.cursor()

        if not new_db:
            try:
                self.create_workflow_metadata_table()
            except:
                pass  # Table might already exist or there might be other issues
            try:
                self._ensure_iterations_columns()
            except:
                pass  # Best-effort migration; absent iterations table is fine

    def _ensure_iterations_columns(self):
        """Add any ArticleData columns missing from an existing ``iterations`` table.

        This lets schema additions (e.g. ``doi``, ``abstract``) apply to databases
        created before the field existed. New columns are appended by ALTER TABLE;
        ``get_iteration_data`` maps rows by name, so the append position is irrelevant.
        Safe to call repeatedly; a no-op if the table does not exist yet.
        """
        found = self.cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='iterations'"
        ).fetchone()
        if not found:
            return
        self.cursor.execute("PRAGMA table_info(iterations)")
        existing = {row[1] for row in self.cursor.fetchall()}
        added = False
        for field in fields(ArticleData):
            if field.name not in existing:
                sql_type = self.SQL_TYPES.get(field.type, 'TEXT')
                self.cursor.execute(f"ALTER TABLE iterations ADD COLUMN {field.name} {sql_type}")
                added = True
        if added:
            self.conn.commit()  # Table might already exist or there might be other issues

    # -------------------------- Helper Methods --------------------------
    def _convert_enum_value(self, value):
        """Helper method to convert enum values to their underlying values for SQLite."""
        if hasattr(value, 'value'):  # Check if it's an enum
            return value.value
        else:
            return value

    # -------------------------- Iteration Table Methods --------------------------
    def check_current_iteration(self):
        """ Check the most recent iteration in the database """
        table_name = "iterations"
        try:
            # iteration is stored as TEXT, so MAX() would compare lexicographically
            # (e.g. MAX('2','10') == '2'). Cast to INTEGER to get the true numeric max.
            self.cursor.execute(f"SELECT MAX(CAST(iteration AS INTEGER)) FROM {table_name}")
            current_iteration = self.cursor.fetchone()[0]
            if current_iteration is None:
                return None
            self.cursor.execute(
                f"SELECT MAX(selected) FROM {table_name} WHERE CAST(iteration AS INTEGER) = ?",
                (current_iteration,),
            )
            max_selected = self.cursor.fetchone()[0]
            self.cursor.execute(
                f"SELECT search_method FROM {table_name} WHERE CAST(iteration AS INTEGER) = ? LIMIT 1",
                (current_iteration,),
            )
            row = self.cursor.fetchone()
            search_method = row[0] if row else None
            return current_iteration, max_selected, search_method
        except Exception as e:
            print(f"Error checking current iteration: {e}")
            self.conn.rollback()
            return None

    def create_iterations_table(self, annotations: List[str] = None):
        # create a table for the iteration if it doesn't exist
        table_name = "iterations"
        try:
            tables_found = self.cursor.execute(
                f"""SELECT name FROM sqlite_master WHERE type='table' AND name='{table_name}'; """
            ).fetchall()

            if tables_found != []:
                # Table exists - make sure any new ArticleData columns (e.g. doi,
                # abstract) are present, then check annotation columns.
                self._ensure_iterations_columns()
                if annotations:
                    # Get existing columns
                    self.cursor.execute(f"PRAGMA table_info({table_name})")
                    existing_columns = [row[1] for row in self.cursor.fetchall()]
                    
                    # Add annotation columns if they don't exist
                    for annotation in annotations:
                        if annotation not in existing_columns:
                            try:
                                self.cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {annotation} TEXT")
                            except Exception as e:
                                # Column might already exist or other error, skip
                                pass
                    self.conn.commit()
                return
            
            field_definitions = []
            for field in fields(ArticleData):
                field_name = field.name
                field_type = field.type
                if field_type not in self.SQL_TYPES:
                    raise ValueError(f"Unsupported field type: {field_type}")
                sql_type = self.SQL_TYPES[field_type]
                field_definitions.append(f"{field_name} {sql_type}")
            
            # Add annotation columns as TEXT (will store JSON strings)
            if annotations:
                for annotation in annotations:
                    field_definitions.append(f"{annotation} TEXT")
                
            create_sql = f"CREATE TABLE IF NOT EXISTS {table_name} ({', '.join(field_definitions)})"
            
            self.cursor.execute(create_sql)
            self.conn.commit()
            
            # Verify table schema
            self.cursor.execute(f"PRAGMA table_info({table_name})")
            schema_info = self.cursor.fetchall()
        except Exception as e:
            self.conn.rollback()
            raise ValueError(f"Failed to create iteration table: {e}")
        
    def insert_iteration_data(self, data: List[ArticleData]):
        table_name = "iterations"
        if len(data) == 0:
            return
        try:
            
            # Copy each object's dict so the type conversions below (enum -> value,
            # None -> "", int -> str) do not mutate the caller's ArticleData objects.
            data_dicts = [dict(data_element.__dict__) for data_element in data]
            for i, data_dict in enumerate(data_dicts):
                for key, value in data_dict.items():
                    if hasattr(value, 'value'):  # Handle enum values
                        data_dict[key] = value.value
                    elif isinstance(value, (list, dict)):
                        data_dict[key] = json.dumps(value)
                    elif value is None:
                        data_dict[key] = ""
                    elif isinstance(value, int) and key in ['id', 'pub_year', 'num_citations']:  # Convert large integers to strings
                        data_dict[key] = str(value)

            columns = ', '.join(data_dicts[0].keys())
            placeholders = ', '.join(['?'] * len(data_dicts[0]))
            sql_query = f"INSERT INTO {table_name} ({columns}) VALUES ({placeholders})"
            
            self.cursor.executemany(sql_query, [tuple(data_dict.values()) for data_dict in data_dicts])
            self.conn.commit()
        except Exception as e:
            self.conn.rollback()
            raise ValueError(f"Could not add elements to table: {e}")
        
    def get_iteration_data(self, **kwargs):
        """
        Get iteration data as dictionaries with field names as keys.
        
        Supports flexible querying with different operators:
        - Simple equality: field=value
        - Not equal: field__ne=value
        - Not empty: field__not_empty=True
        - Empty: field__empty=True
        - Greater than: field__gt=value
        - Less than: field__lt=value
        - Greater or equal: field__gte=value
        - Less or equal: field__lte=value
        - Like: field__like=pattern
        - In: field__in=[value1, value2, ...]
        - Not in: field__nin=[value1, value2, ...]
        
        Examples:
        - get_iteration_data(iteration=1)  # iteration = 1
        - get_iteration_data(bibtex__not_empty=True)  # bibtex != ""
        - get_iteration_data(title__ne="")  # title != ""
        - get_iteration_data(iteration__gt=0)  # iteration > 0
        """
        table_name = "iterations"
        try:
            # Rows are mapped positionally against fields(ArticleData) below, so no
            # row_factory is needed here.
            if kwargs:
                conditions = []
                values = []
                
                for key, value in kwargs.items():
                    if '__' in key:
                        field_name, operator = key.split('__', 1)
                    else:
                        field_name, operator = key, 'eq'
                    
                    # Handle special operators
                    if operator == 'not_empty':
                        conditions.append(f"{field_name} != '' AND {field_name} IS NOT NULL")
                    elif operator == 'empty':
                        conditions.append(f"({field_name} = '' OR {field_name} IS NULL)")
                    elif operator == 'ne':
                        conditions.append(f"{field_name} != ?")
                        values.append(self._convert_enum_value(value))
                    elif operator == 'gt':
                        conditions.append(f"{field_name} > ?")
                        values.append(self._convert_enum_value(value))
                    elif operator == 'lt':
                        conditions.append(f"{field_name} < ?")
                        values.append(self._convert_enum_value(value))
                    elif operator == 'gte':
                        conditions.append(f"{field_name} >= ?")
                        values.append(self._convert_enum_value(value))
                    elif operator == 'lte':
                        conditions.append(f"{field_name} <= ?")
                        values.append(self._convert_enum_value(value))
                    elif operator == 'like':
                        conditions.append(f"lower({field_name}) LIKE ?")
                        values.append(self._convert_enum_value(value))
                    elif operator == 'in':
                        placeholders = ','.join(['?' for _ in value])
                        conditions.append(f"{field_name} IN ({placeholders})")
                        values.extend([self._convert_enum_value(v) for v in value])
                    elif operator == 'nin':
                        placeholders = ','.join(['?' for _ in value])
                        conditions.append(f"{field_name} NOT IN ({placeholders})")
                        values.extend([self._convert_enum_value(v) for v in value])
                    else:  # default to equality
                        conditions.append(f"{field_name} = ?")
                        values.append(self._convert_enum_value(value))
                
                sql_query = f"SELECT * FROM {table_name} WHERE {' AND '.join(conditions)}"
                self.cursor.execute(sql_query, values)
            else:
                self.cursor.execute(f"SELECT * FROM {table_name}")
            
            # Map rows to ArticleData *by column name* (not position), so the mapping
            # is robust to extra columns (annotations) and to schema additions such as
            # doi/abstract appended by ALTER TABLE on pre-existing databases.
            columns = [desc[0] for desc in self.cursor.description]
            field_names = {f.name for f in fields(ArticleData)}
            rows = self.cursor.fetchall()
            dict_list = []
            for row in rows:
                row_map = dict(zip(columns, row))
                article_kwargs = {name: row_map[name] for name in field_names if name in row_map}
                dict_list.append(ArticleData(**article_kwargs))
            return dict_list
        except Exception as e:
            print("Error getting iteration data: ", e)
            self.conn.rollback()
            raise ValueError(f"Failed to get iteration data: {e}")
    
    def update_iteration_data(self, iteration: int, article_id: str = "", **kwargs):
        table_name = "iterations"
        try:
            # Normalize values for SQLite storage first, then build the query once.
            #   enum   -> underlying value
            #   list/dict -> JSON string
            #   None   -> ""
            #   bool   -> 1/0 (so keep_title/keep_content round-trip correctly)
            #   int    -> str (all integer columns are stored as TEXT)
            for key, value in kwargs.items():
                if hasattr(value, 'value'):
                    kwargs[key] = value.value
                elif isinstance(value, (list, dict)):
                    kwargs[key] = json.dumps(value)
                elif value is None:
                    kwargs[key] = ""
                elif isinstance(value, bool):
                    kwargs[key] = 1 if value else 0
                elif isinstance(value, int):
                    kwargs[key] = str(value)

            assignments = ', '.join([f"{key} = ?" for key in kwargs])
            if article_id != "":
                sql_query = f"UPDATE {table_name} SET {assignments} WHERE id = ? and iteration = ?"
                values = list(kwargs.values()) + [article_id, iteration]
            else:
                sql_query = f"UPDATE {table_name} SET {assignments} WHERE iteration = ?"
                values = list(kwargs.values()) + [iteration]
            self.cursor.execute(sql_query, values)
            self.conn.commit()
        except Exception as e:
            self.conn.rollback()
            raise ValueError(f"Failed to update iteration data: {e}")

    @staticmethod
    def _interpret_keep(val) -> bool | None:
        """Map a stored keep_* cell to a tri-state decision:
        None  -> undecided (NULL / "" : the article was never screened at this stage)
        True  -> kept, False -> rejected.
        This deliberately distinguishes "undecided" from "rejected" (both of which
        used to collapse to False), so callers can tell a pending article from a
        rejected one."""
        if val is None or val == '':
            return None
        if val in (1, True, '1', 1.0, 'True', 'true'):
            return True
        if val in (0, False, '0', 0.0, 'False', 'false'):
            return False
        return bool(val)

    def get_keep_title(self, article_id: str, iteration: int) -> bool | None:
        """Read keep_title from iterations table by explicit column name (avoids column-order issues).
        Returns True (kept), False (rejected), or None (undecided / no row)."""
        try:
            self.cursor.execute(
                "SELECT keep_title FROM iterations WHERE id = ? AND iteration = ?",
                (article_id, iteration)
            )
            row = self.cursor.fetchone()
            if row is None:
                return None
            return self._interpret_keep(row[0])
        except Exception:
            return None

    def get_keep_content(self, article_id: str, iteration: int) -> bool | None:
        """Read keep_content from iterations table by explicit column name.
        Returns True (kept), False (rejected), or None (undecided / no row)."""
        try:
            self.cursor.execute(
                "SELECT keep_content FROM iterations WHERE id = ? AND iteration = ?",
                (article_id, iteration)
            )
            row = self.cursor.fetchone()
            if row is None:
                return None
            return self._interpret_keep(row[0])
        except Exception:
            return None

    def update_batch_iteration_data(self, iteration: int, update_data: List[Tuple[str, any, str]]):
        table_name = "iterations"
        try:
            updates_by_column = defaultdict(list)
            for article_id, new_value, column_name in update_data:
                # Convert values to appropriate types for SQLite
                if new_value is None:
                    sql_value = None
                elif isinstance(new_value, bool):
                    sql_value = int(new_value)  # Convert bool to int for SQLite
                elif hasattr(new_value, 'value'):  # Handle Enum values
                    sql_value = new_value.value  # Get the underlying value (e.g., 4 for SELECTED)
                else:
                    sql_value = str(new_value)  # Convert everything else to string
                
                updates_by_column[column_name].append((sql_value, article_id, iteration))
            
            for column_name, column_updates in updates_by_column.items():
                query = f"UPDATE {table_name} SET {column_name} = ? WHERE id = ? and iteration = ?"
                self.cursor.executemany(query, column_updates)
            self.conn.commit()
        except Exception as e:
            self.conn.rollback()
            raise ValueError(f"Failed to update batch iteration data: {e}")

    def delete_batch_iteration_data(self, iteration: int, delete_data: List):
        table_name = "iterations"
        try:
            self.cursor.executemany(f"DELETE FROM {table_name} WHERE title = ? and iteration = ?", [(title, iteration) for title in delete_data])
            self.conn.commit()
        except Exception as e:
            self.conn.rollback()
            raise ValueError(f"Failed to delete batch iteration data: {e}")

    def clear_unidentified_articles(self, iteration: int):
        table_name = "iterations"
        try:
            self.cursor.execute(f"DELETE FROM {table_name} WHERE id = '' AND iteration = ?", (iteration,))
            self.conn.commit()
        except Exception as e:
            self.conn.rollback()
            raise ValueError(f"Failed to clear unidentified articles: {e}")

    # --------------------------- Screening Table Methods --------------------------
    def create_screening_table(self, annotations: List[str]):
        annotation_columns = []
        for annotation in annotations:
            annotation_columns.append(f"{annotation} TEXT")
        annotation_columns_str = ", ".join(annotation_columns) if annotation_columns else ""
        table_name = "screening"
        try:
            tables_found = self.cursor.execute(
                f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table_name}'; "
            ).fetchall()
            
            # If table exists, check if it has the correct structure (composite primary key)
            # Only drop and recreate if the structure is wrong
            if tables_found != []:
                # Check if table has composite primary key
                self.cursor.execute(f"SELECT sql FROM sqlite_master WHERE type='table' AND name='{table_name}'")
                create_sql_result = self.cursor.fetchone()
                if create_sql_result and create_sql_result[0]:
                    create_sql = create_sql_result[0]
                    if 'PRIMARY KEY(id, rater)' not in create_sql.replace('\n', ' ').replace('  ', ' '):
                        # Table exists but doesn't have composite primary key - drop and recreate
                        print(f"WARNING: Dropping {table_name} table to recreate with composite primary key")
                        self.cursor.execute(f"DROP TABLE IF EXISTS {table_name}")
                    else:
                        # Table exists with correct structure - just ensure annotation columns and title column exist
                        self.cursor.execute(f"PRAGMA table_info({table_name})")
                        columns_info = self.cursor.fetchall()
                        existing_columns = [col[1] for col in columns_info]
                        # Add title column if it doesn't exist
                        if "title" not in existing_columns:
                            try:
                                self.cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN title TEXT")
                            except sqlite3.OperationalError:
                                pass  # Column might already exist
                        # Add annotation columns if they don't exist
                        for annotation in annotations:
                            if annotation not in existing_columns:
                                try:
                                    self.cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {annotation} TEXT")
                                except sqlite3.OperationalError:
                                    pass  # Column might already exist
                        self.conn.commit()
                        return
            
            # Composite primary key on (id, rater)
            if annotation_columns_str:
                create_query = f"CREATE TABLE IF NOT EXISTS screening \
(id TEXT, rater TEXT, iteration INTEGER, title TEXT,\
keep_title BOOLEAN, reason_title  TEXT,\
keep_content BOOLEAN, reason_content TEXT, {annotation_columns_str},\
title_settled BOOLEAN, content_settled BOOLEAN,\
PRIMARY KEY(id, rater))"
            else:
                create_query = f"CREATE TABLE IF NOT EXISTS screening \
(id TEXT, rater TEXT, iteration INTEGER, title TEXT,\
keep_title BOOLEAN, reason_title  TEXT,\
keep_content BOOLEAN, reason_content TEXT,\
title_settled BOOLEAN, content_settled BOOLEAN,\
PRIMARY KEY(id, rater))"
            
            self.cursor.execute(create_query)
            self.conn.commit()
        except Exception as e:
            self.conn.rollback()
            raise ValueError(f"Failed to create screening table: {e}")
    
    def insert_screening_data(
        self, 
        article_id: str, 
        rater: str, 
        iteration: int, 
        keep: bool, 
        reason: str,
        settled: bool = False,
        screening_phase: str="title",
        title: str = "",
        **annotations: str
    ):
        table_name = "screening"
        if not self.cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table_name}'").fetchone():
            self.create_screening_table(list(annotations.keys()))
        try:
            keep_int = 1 if keep else 0
            title_settled_int = 1 if screening_phase == "title" and settled else 0
            content_settled_int = 1 if screening_phase == "content" and settled else 0
            keep_key = f"keep_{screening_phase}"
            reason_key = f"reason_{screening_phase}"
            settle_key = f"{screening_phase}_settled"
            
            annotation_keys = list(annotations.keys())
            
            columns = ["id", "rater", "iteration", "title", "keep_title", "reason_title", "title_settled", "keep_content", "reason_content", "content_settled"]
            if annotation_keys:
                columns.extend(annotation_keys)
            
            placeholders = ["?", "?", "?", "?", "?", "?", "?", "?", "?", "?"]
            if annotation_keys:
                placeholders.extend(["?"] * len(annotation_keys))
            
            update_clauses = ["title = ?", f"{keep_key} = ?", f"{reason_key} = ?", f"{settle_key} = ?"]
            if annotation_keys:
                update_clauses.extend([f"{key} = ?" for key in annotation_keys])
            
            def _safe_str(v):
                if v is None:
                    return ""
                if isinstance(v, (list, dict)):
                    return json.dumps(v)
                return str(v)

            keep_title_val = (1 if keep else 0) if screening_phase == "title" else None
            reason_title_val = _safe_str(reason) if screening_phase == "title" else None
            keep_content_val = (1 if keep else 0) if screening_phase == "content" else None
            reason_content_val = _safe_str(reason) if screening_phase == "content" else None

            insert_values = [
                _safe_str(article_id),
                _safe_str(rater),
                int(iteration),
                _safe_str(title),
                keep_title_val,
                reason_title_val,
                title_settled_int,
                keep_content_val,
                reason_content_val,
                content_settled_int,
            ]
            if annotation_keys:
                insert_values.extend([_safe_str(annotations[key]) for key in annotation_keys])

            update_values = [
                _safe_str(title),
                keep_int,
                _safe_str(reason),
                title_settled_int if screening_phase == "title" else content_settled_int,
            ]
            if annotation_keys:
                update_values.extend([_safe_str(annotations[key]) for key in annotation_keys])

            query = f"INSERT INTO {table_name} ({', '.join(columns)}) VALUES ({', '.join(placeholders)}) ON CONFLICT(id, rater) DO UPDATE SET {', '.join(update_clauses)}"
            values = tuple(insert_values + update_values)
            
            self.cursor.execute(query, values)
            self.conn.commit()
        except Exception as e:
            self.conn.rollback()
            raise ValueError(f"Failed to insert screening data: {e}")
    
    def get_all_screening_rows_for_iteration(self, iteration: int):
        """Return all screening rows for an iteration (no filter on settled). Used for listing all disagreements."""
        table_name = "screening"
        original_row_factory = self.conn.row_factory
        try:
            self.conn.row_factory = sqlite3.Row
            cursor = self.conn.cursor()
            query = f"SELECT * FROM {table_name} WHERE iteration = ?"
            cursor.execute(query, (iteration,))
            column_names = [description[0] for description in cursor.description]
            rows = cursor.fetchall()
            result = []
            for row in rows:
                if isinstance(row, sqlite3.Row):
                    row_dict = {key: row[key] for key in row.keys()}
                else:
                    row_dict = dict(zip(column_names, row))
                result.append(row_dict)
            cursor.close()
            return result
        finally:
            self.conn.row_factory = original_row_factory

    def get_screening_data(self, iteration: int, title_settled: bool = False, content_settled: bool = False):
        table_name = "screening"
        original_row_factory = self.conn.row_factory
        try:
            self.conn.row_factory = sqlite3.Row
            cursor = self.conn.cursor()
            
            title_settled_int = 1 if title_settled else 0
            content_settled_int = 1 if content_settled else 0

            query = f"SELECT * FROM {table_name} WHERE iteration = ? AND title_settled = ? AND content_settled = ?"
            values = (iteration, title_settled_int, content_settled_int)
            cursor.execute(query, values)

            column_names = [description[0] for description in cursor.description]
            rows = cursor.fetchall()
            result = []
            for row in rows:
                if isinstance(row, sqlite3.Row):
                    row_dict = {key: row[key] for key in row.keys()}
                else:
                    row_dict = dict(zip(column_names, row))
                result.append(row_dict)
            cursor.close()
            return result
        except Exception as e:
            self.conn.rollback()
            raise ValueError(f"Failed to get screening data: {e}")
        finally:
            self.conn.row_factory = original_row_factory

    def update_screening_data(self, iteration: int, article_id: str, rater: str, **kwargs):
        table_name = "screening"
        try:
            assignments = ', '.join([f"{key} = ?" for key in kwargs.keys()])
            sql_query = f"UPDATE {table_name} SET {assignments} WHERE id = ? and iteration = ? and rater = ?"
            # Convert boolean values to integers for SQLite
            values = []
            for key in kwargs:
                value = kwargs[key]
                # Convert boolean to int for SQLite (False = 0, True = 1)
                if isinstance(value, bool):
                    values.append(1 if value else 0)
                else:
                    values.append(value)
            self.cursor.execute(sql_query, values + [article_id, iteration, rater])
            self.conn.commit()
        except Exception as e:
            self.conn.rollback()
            raise ValueError(f"Failed to update screening data: {e}")

    def get_agreements_screening_data(
        self, 
        iteration: int, 
        title_settled: bool = False, 
        content_settled: bool = False,
        phase: str = "title",
        raters: List[str] = None
    ):
        """ 
        Searches in the screening table for all rows with the same article_id and iteration.
        Only returns rows if all raters agree on the 'keep' value (i.e., all rows have the same keep value).
        If there are different keep values for the same article_id, returns an empty list.
        If no raters are provided, consider all raters.
        If rater are provided, do not consider the rows of raters not in the list.
        """
        table_name = "screening"
        original_row_factory = self.conn.row_factory
        try:
            self.conn.row_factory = sqlite3.Row
            cursor = self.conn.cursor()
            
            title_settled_int = 1 if title_settled else 0
            content_settled_int = 1 if content_settled else 0
            keep_key = f"keep_{phase}"
            # Normalize keep value (1/'1'/True -> 1, 0/'0'/False -> 0) so "all agree" is detected correctly.
            # Only treat as agreement when every (considered) rater has a value and they are all the same.
            if raters is None:
                query = f"""    
                SELECT s1.* FROM {table_name} s1
                WHERE s1.iteration = ? AND s1.title_settled = ? AND s1.content_settled = ?
                AND (
                    SELECT COUNT(*) FROM {table_name} s2
                    WHERE s2.iteration = s1.iteration AND s2.id = s1.id
                ) = (
                    SELECT COUNT(*) FROM {table_name} s2
                    WHERE s2.iteration = s1.iteration AND s2.id = s1.id
                    AND s2.{keep_key} IS NOT NULL AND s2.{keep_key} != ''
                )
                AND (
                    SELECT COUNT(DISTINCT CAST(s2.{keep_key} AS INTEGER))
                    FROM {table_name} s2
                    WHERE s2.iteration = s1.iteration AND s2.id = s1.id
                    AND s2.{keep_key} IS NOT NULL AND s2.{keep_key} != ''
                ) = 1
                ORDER BY s1.id
                """
                values = (iteration, title_settled_int, content_settled_int)
            else:
                rater_placeholders = ", ".join("?" for _ in raters)
                query = f"""
                SELECT s1.* FROM {table_name} s1
                WHERE s1.iteration = ? AND s1.title_settled = ? AND s1.content_settled = ?
                AND s1.rater IN ({rater_placeholders})
                AND (
                    SELECT COUNT(*) FROM {table_name} s2
                    WHERE s2.iteration = s1.iteration AND s2.id = s1.id
                    AND s2.rater IN ({rater_placeholders})
                ) = (
                    SELECT COUNT(*) FROM {table_name} s2
                    WHERE s2.iteration = s1.iteration AND s2.id = s1.id
                    AND s2.rater IN ({rater_placeholders})
                    AND s2.{keep_key} IS NOT NULL AND s2.{keep_key} != ''
                )
                AND (
                    SELECT COUNT(DISTINCT CAST(s2.{keep_key} AS INTEGER))
                    FROM {table_name} s2
                    WHERE s2.iteration = s1.iteration AND s2.id = s1.id
                    AND s2.rater IN ({rater_placeholders})
                    AND s2.{keep_key} IS NOT NULL AND s2.{keep_key} != ''
                ) = 1
                ORDER BY s1.id
                """
                values = (
                    iteration,
                    title_settled_int,
                    content_settled_int,
                    *raters,
                    *raters,
                    *raters,
                    *raters,
                )
            cursor.execute(query, values)
            
            column_names = [description[0] for description in cursor.description]
            rows = cursor.fetchall()
            result = []
            for row in rows:
                if isinstance(row, sqlite3.Row):
                    row_dict = {key: row[key] for key in row.keys()}
                else:
                    row_dict = dict(zip(column_names, row))
                result.append(row_dict)
            cursor.close()
            return result
        except Exception as e:
            self.conn.rollback()
            raise ValueError(f"Failed to get agreements screening data: {e}")
        finally:
            self.conn.row_factory = original_row_factory

    def get_disagreements_screening_data(
        self, 
        iteration: int, 
        title_settled: bool = False, 
        content_settled: bool = False,
        phase: str = "title"
    ):
        table_name = "screening"
        original_row_factory = self.conn.row_factory
        try:
            self.conn.row_factory = sqlite3.Row
            cursor = self.conn.cursor()
            
            title_settled_int = 1 if title_settled else 0
            content_settled_int = 1 if content_settled else 0
            keep_key = f"keep_{phase}"
            # Normalize keep value so 1/'1'/True count as one value; only true disagreements (mixed 0 and 1) are returned
            query = f"""
                SELECT s1.* FROM {table_name} s1
                WHERE s1.iteration = ? AND s1.title_settled = ? AND s1.content_settled = ?
                AND (
                    SELECT COUNT(DISTINCT CAST(s2.{keep_key} AS INTEGER))
                    FROM {table_name} s2
                    WHERE s2.iteration = s1.iteration AND s2.id = s1.id
                    AND s2.{keep_key} IS NOT NULL AND s2.{keep_key} != ''
                ) > 1
                ORDER BY s1.id
            """
            values = (iteration, title_settled_int, content_settled_int)
            cursor.execute(query, values)
            
            column_names = [description[0] for description in cursor.description]
            rows = cursor.fetchall()
            result = []
            for row in rows:
                if isinstance(row, sqlite3.Row):
                    row_dict = {key: row[key] for key in row.keys()}
                else:
                    row_dict = dict(zip(column_names, row))
                result.append(row_dict)
            cursor.close()
            return result
        except Exception as e:
            self.conn.rollback()
            raise ValueError(f"Failed to get disagreements screening data: {e}")
        finally:
            self.conn.row_factory = original_row_factory

    def get_all_disagreements_screening_data(self, iteration: int, phase: str) -> List[dict]:
        """
        Return screening rows for all articles that have a disagreement in this phase
        (unsettled and already settled), so the CLI can show the full list and allow
        changing previously settled decisions. For title phase filters content_settled=0;
        for content phase filters title_settled=1.
        """
        table_name = "screening"
        original_row_factory = self.conn.row_factory
        try:
            self.conn.row_factory = sqlite3.Row
            cursor = self.conn.cursor()
            keep_key = f"keep_{phase}"
            phase_settled_key = f"{phase}_settled"
            # Title phase: only rows with content_settled=0. Content phase: only title_settled=1.
            if phase == "title":
                base_conditions = "s1.iteration = ? AND s1.content_settled = 0"
                params: Tuple = (iteration,)
            else:
                base_conditions = "s1.iteration = ? AND s1.title_settled = 1"
                params = (iteration,)
            query = f"""
                SELECT s1.* FROM {table_name} s1
                WHERE {base_conditions}
                AND (
                    SELECT COUNT(DISTINCT CAST(s2.{keep_key} AS INTEGER))
                    FROM {table_name} s2
                    WHERE s2.iteration = s1.iteration AND s2.id = s1.id
                    AND s2.{keep_key} IS NOT NULL AND s2.{keep_key} != ''
                ) > 1
                ORDER BY s1.{phase_settled_key}, s1.id
            """
            cursor.execute(query, params)
            column_names = [d[0] for d in cursor.description]
            rows = cursor.fetchall()
            result = []
            for row in rows:
                if isinstance(row, sqlite3.Row):
                    row_dict = {key: row[key] for key in row.keys()}
                else:
                    row_dict = dict(zip(column_names, row))
                result.append(row_dict)
            cursor.close()
            return result
        except Exception as e:
            self.conn.rollback()
            raise ValueError(f"Failed to get all disagreements screening data: {e}")
        finally:
            self.conn.row_factory = original_row_factory

    def settle_screening_data(self, iteration: int, article_id: str, settled: bool = False, phase: str = "title"):
        table_name = "screening"
        try:
            # Convert boolean to int for SQLite (False = 0, True = 1)
            settled_int = 1 if settled else 0
            phase_key = f"{phase}_settled"
            self.cursor.execute(f"UPDATE {table_name} SET {phase_key} = ? WHERE iteration = ? AND id = ?", (settled_int, iteration, article_id))
            self.conn.commit()
        except Exception as e:
            self.conn.rollback()
            raise ValueError(f"Failed to settle screening data: {e}")

    def sync_iteration_from_settled_screening(
        self, iteration: int, phase: str, selection_stage: SelectionStage
    ) -> None:
        """
        Ensure the iterations table matches screening for all articles that are already
        settled for this phase. For each article where every rater has phase_settled=1,
        if there is a single consensus keep_* value, set iterations.selected and keep_*
        accordingly. Fixes cases where screening was settled but iterations was not updated.
        """
        table_name = "screening"
        phase_settled_key = f"{phase}_settled"
        keep_key = f"keep_{phase}"
        try:
            # Article ids where all screening rows have phase_settled=1
            self.cursor.execute(
                f"""
                SELECT id FROM {table_name}
                WHERE iteration = ?
                GROUP BY id
                HAVING MIN(CAST({phase_settled_key} AS INTEGER)) = 1
                  AND MAX(CAST({phase_settled_key} AS INTEGER)) = 1
                """,
                (str(iteration),),
            )
            settled_ids = [row[0] for row in self.cursor.fetchall()]
            for article_id in settled_ids:
                self.cursor.execute(
                    f"""
                    SELECT COUNT(DISTINCT CAST({keep_key} AS INTEGER)),
                           MAX(CAST({keep_key} AS INTEGER))
                    FROM {table_name}
                    WHERE iteration = ? AND id = ?
                    AND {keep_key} IS NOT NULL AND {keep_key} != ''
                    """,
                    (str(iteration), article_id),
                )
                row = self.cursor.fetchone()
                if not row or row[0] == 0:
                    continue
                distinct_count, consensus_int = row[0], row[1]
                if distinct_count != 1:
                    continue  # Resolved disagreement; iterations already set by user
                keep_value = bool(consensus_int)
                selected = selection_stage.value if keep_value else (selection_stage.value - 1)
                self.update_iteration_data(iteration, article_id, selected=selected, **{keep_key: keep_value})
        except Exception as e:
            self.conn.rollback()
            raise ValueError(f"Failed to sync iteration from settled screening: {e}")

    def get_screening_data_for_one_article(self, article_id: str, iteration: int, rater: str, phase: str, phase_settled: bool = False):
        table_name = "screening"
        try:
            self.cursor.execute(f"SELECT * FROM {table_name} WHERE id = ? AND iteration = ? AND rater = ? AND {phase}_settled = ?", (article_id, iteration, rater, phase_settled))
            return self.cursor.fetchone()
        except Exception as e:
            self.conn.rollback()
            raise ValueError(f"Failed to get screening data for article: {e}")
    
    def get_screening_data_for_articles(self, article_ids: List[str], iteration: int, rater: str, phase: str, keep: bool = True):
        table_name = "screening"
        try:
            if not article_ids:
                return []
            placeholders = ", ".join("?" for _ in article_ids)
            query = f"SELECT * FROM {table_name} WHERE id IN ({placeholders}) AND iteration = ? AND rater = ? AND keep_{phase} = ?"
            self.cursor.execute(query, (*article_ids, iteration, rater, 1 if keep else 0))
            rows = self.cursor.fetchall()
            column_names = [d[0] for d in self.cursor.description]
            return [dict(zip(column_names, row)) for row in rows]
        except Exception as e:
            self.conn.rollback()
            raise ValueError(f"Failed to get screening data for articles: {article_ids}: {e}")

    def get_screening_data_for_rater(
        self, article_ids: List[str], iteration: int, rater: str, phase: str
    ) -> List[dict]:
        """
        Return screening rows for the given rater and articles that have a decision for the given phase.
        phase: "title" or "content" – only rows with keep_{phase} set (from that screening stage) are returned.
        Used to pre-fill / edit when re-running title or content screening.
        """
        table_name = "screening"
        try:
            if not article_ids:
                return []
            placeholders = ", ".join("?" for _ in article_ids)
            self.cursor.execute(
                f"SELECT * FROM {table_name} WHERE id IN ({placeholders}) AND iteration = ? AND rater = ? AND keep_{phase} IS NOT NULL",
                (*article_ids, iteration, rater)
            )
            rows = self.cursor.fetchall()
            column_names = [d[0] for d in self.cursor.description]
            return [dict(zip(column_names, row)) for row in rows]
        except Exception as e:
            self.conn.rollback()
            raise ValueError(f"Failed to get screening data for rater: {e}")

    # -------------------------- Final Annotations Table Methods --------------------------
    def create_annotations_table(self, annotations: List[str]):
        """Create table for final annotations (one row per accepted article per iteration)."""
        table_name = "annotations"
        try:
            tables_found = self.cursor.execute(
                f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table_name}';"
            ).fetchall()

            if tables_found != []:
                # Table exists - ensure annotation columns exist
                self.cursor.execute(f"PRAGMA table_info({table_name})")
                existing_columns = [row[1] for row in self.cursor.fetchall()]
                for annotation in annotations:
                    if annotation not in existing_columns:
                        try:
                            self.cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {annotation} TEXT")
                        except sqlite3.OperationalError:
                            pass
                self.conn.commit()
                return

            annotation_columns = ", ".join([f"{a} TEXT" for a in annotations]) if annotations else ""
            columns = f"id TEXT, iteration INTEGER{', ' + annotation_columns if annotation_columns else ''}, PRIMARY KEY(id, iteration)"
            self.cursor.execute(f"CREATE TABLE IF NOT EXISTS {table_name} ({columns})")
            self.conn.commit()
        except Exception as e:
            self.conn.rollback()
            raise ValueError(f"Failed to create annotations table: {e}")

    def insert_annotations_data(self, article_id: str, iteration: int, **annotation_values: str):
        """Insert or replace final annotations for an accepted article."""
        table_name = "annotations"
        if not self.cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table_name}'").fetchone():
            self.create_annotations_table(list(annotation_values.keys()))
        try:
            keys = ["id", "iteration"] + list(annotation_values.keys())
            columns = ", ".join(keys)
            placeholders = ", ".join(["?"] * len(keys))
            update_clauses = ", ".join([f"{k} = excluded.{k}" for k in annotation_values.keys()])
            query = f"INSERT INTO {table_name} ({columns}) VALUES ({placeholders}) ON CONFLICT(id, iteration) DO UPDATE SET {update_clauses}"
            values = [article_id, iteration] + [annotation_values.get(k, "") or "" for k in annotation_values.keys()]
            self.cursor.execute(query, values)
            self.conn.commit()
        except Exception as e:
            self.conn.rollback()
            raise ValueError(f"Failed to insert annotations data: {e}")

    def get_annotations_data(self, iteration: int, article_id: str = None):
        """Get final annotations for an iteration, optionally for one article."""
        table_name = "annotations"
        try:
            self.conn.row_factory = sqlite3.Row
            if article_id:
                self.cursor.execute(f"SELECT * FROM {table_name} WHERE iteration = ? AND id = ?", (iteration, article_id))
            else:
                self.cursor.execute(f"SELECT * FROM {table_name} WHERE iteration = ?", (iteration,))
            rows = self.cursor.fetchall()
            column_names = [d[0] for d in self.cursor.description]
            result = []
            for row in rows:
                result.append(dict(zip(column_names, row)))
            return result
        except Exception as e:
            self.conn.rollback()
            raise ValueError(f"Failed to get annotations data: {e}")
        finally:
            self.conn.row_factory = None

    def get_screening_rows_for_article(self, article_id: str, iteration: int):
        """Get all screening rows for one article (all raters) for gathering annotations."""
        table_name = "screening"
        try:
            self.conn.row_factory = sqlite3.Row
            self.cursor.execute(f"SELECT * FROM {table_name} WHERE id = ? AND iteration = ?", (article_id, iteration))
            rows = self.cursor.fetchall()
            column_names = [d[0] for d in self.cursor.description]
            return [dict(zip(column_names, row)) for row in rows]
        except Exception as e:
            self.conn.rollback()
            raise ValueError(f"Failed to get screening rows for article: {e}")
        finally:
            self.conn.row_factory = None

    def get_screening_raters(self) -> List[str]:
        """Get distinct rater names from the screening table."""
        table_name = "screening"
        try:
            self.cursor.execute(f"SELECT DISTINCT rater FROM {table_name} ORDER BY rater")
            rows = self.cursor.fetchall()
            return [row[0] or "" for row in rows if row[0]]
        except Exception as e:
            self.conn.rollback()
            raise ValueError(f"Failed to get screening raters: {e}")

    def get_screened_article_ids(self, iteration: int, rater: str, phase: str = "title") -> List[str]:
        """Get article ids that already have a screening decision for this iteration and rater (excluded when resuming).
        phase: 'title' = any row (title screened); 'content' = row with content decision (reason_content not null)."""
        table_name = "screening"
        try:
            if phase == "content":
                self.cursor.execute(
                    f"SELECT DISTINCT id FROM {table_name} WHERE iteration = ? AND rater = ? AND reason_content IS NOT NULL AND reason_content != ''",
                    (iteration, rater)
                )
            else:
                self.cursor.execute(f"SELECT DISTINCT id FROM {table_name} WHERE iteration = ? AND rater = ?", (iteration, rater))
            rows = self.cursor.fetchall()
            return [row[0] or "" for row in rows if row[0]]
        except Exception as e:
            self.conn.rollback()
            raise ValueError(f"Failed to get screened article ids: {e}")

    def get_screening_rows_by_rater(self, rater: str, iteration: int = None) -> List[dict]:
        """Get all screening rows for a given rater, optionally filtered by iteration."""
        table_name = "screening"
        try:
            self.conn.row_factory = sqlite3.Row
            if iteration is not None:
                self.cursor.execute(f"SELECT * FROM {table_name} WHERE rater = ? AND iteration = ? ORDER BY iteration, id", (rater, iteration))
            else:
                self.cursor.execute(f"SELECT * FROM {table_name} WHERE rater = ? ORDER BY iteration, id", (rater,))
            rows = self.cursor.fetchall()
            column_names = [d[0] for d in self.cursor.description]
            return [dict(zip(column_names, row)) for row in rows]
        except Exception as e:
            self.conn.rollback()
            raise ValueError(f"Failed to get screening rows by rater: {e}")
        finally:
            self.conn.row_factory = None

    def get_all_annotations_data(self) -> List[dict]:
        """Get all rows from the annotations table (all iterations)."""
        table_name = "annotations"
        try:
            self.conn.row_factory = sqlite3.Row
            self.cursor.execute(f"SELECT * FROM {table_name} ORDER BY iteration, id")
            rows = self.cursor.fetchall()
            if not rows:
                return []
            column_names = [d[0] for d in self.cursor.description]
            return [dict(zip(column_names, row)) for row in rows]
        except Exception as e:
            self.conn.rollback()
            raise ValueError(f"Failed to get annotations data: {e}")
        finally:
            self.conn.row_factory = None

    def get_all_annotations_data_with_titles(self) -> List[dict]:
        """Get all annotation rows with title from iterations table. Column order: id, title, iteration, then annotation columns."""
        try:
            self.conn.row_factory = sqlite3.Row
            self.cursor.execute(
                "SELECT a.*, i.title FROM annotations a "
                "LEFT JOIN iterations i ON a.id = i.id AND a.iteration = i.iteration "
                "ORDER BY a.iteration, a.id"
            )
            rows = self.cursor.fetchall()
            if not rows:
                return []
            column_names = [d[0] for d in self.cursor.description]
            # desired order: id, title, iteration, then other annotation columns (excluding id, iteration)
            annotation_cols = [c for c in column_names if c not in ("id", "title", "iteration")]
            desired_order = ["id", "title", "iteration"] + annotation_cols
            result = []
            for row in rows:
                row_dict = dict(zip(column_names, row))
                result.append({k: row_dict.get(k) for k in desired_order})
            return result
        except Exception as e:
            self.conn.rollback()
            raise ValueError(f"Failed to get annotations data with titles: {e}")
        finally:
            self.conn.row_factory = None

    # -------------------------- Seen Titles Table Methods --------------------------
    
    def create_seen_titles_table(self):
        table_name = "seen_titles"
        try:
            tables_found = self.cursor.execute(
                f"""SELECT name FROM sqlite_master WHERE type='table' AND name='{table_name}'; """
            ).fetchall()
            if tables_found != []:
                return
            self.cursor.execute("CREATE TABLE IF NOT EXISTS seen_titles (title TEXT PRIMARY KEY, id TEXT)")
            self.conn.commit()
        except Exception as e:
            self.conn.rollback()
            raise ValueError(f"Failed to create seen titles table: {e}")

    def insert_seen_titles_data(self, data: List[Tuple[str, str]]):
        # data is a list of tuples (title, id)
        table_name = "seen_titles"
        if len(data) == 0:
            return
        try:
            # Convert integer IDs to strings to prevent overflow
            converted_data = []
            for i, (title, article_id) in enumerate(data):
                title = title.lower()
                if isinstance(article_id, int):
                    converted_data.append((title, str(article_id)))
                else:
                    converted_data.append((title, article_id))
            
            # Use INSERT OR IGNORE to skip duplicates, or INSERT OR REPLACE to update existing entries
            # Change to INSERT OR REPLACE if you want to update existing entries instead
            self.cursor.executemany(f"INSERT OR IGNORE INTO {table_name} (title, id) VALUES (?, ?)", converted_data)
            self.conn.commit()
        except Exception as e:
            self.conn.rollback()
            raise ValueError(f"Failed to insert seen titles data: {e}")

    def get_seen_titles_data(self):
        table_name = "seen_titles"
        try:
            self.cursor.execute(f"SELECT * FROM {table_name}")
            return self.cursor.fetchall()
        except Exception as e:
            self.conn.rollback()
            raise ValueError(f"Failed to get seen titles data: {e}")
        
    def get_seen_title(self, title: str):
        table_name = "seen_titles"
        title = title.lower()
        try:
            self.cursor.execute(f"SELECT * FROM {table_name} WHERE title = ?", (title,))
            return self.cursor.fetchone()
        except Exception as e:
            self.conn.rollback()
            raise ValueError(f"Failed to get seen title: {e}")

    # -------------------------- Conf Rank Table Methods --------------------------

    def create_conf_rank_table(self):
        table_name = "conf_rank"
        try:
            tables_found = self.cursor.execute(
                f"""SELECT name FROM sqlite_master WHERE type='table' AND name='{table_name}'; """
            ).fetchall()
            if tables_found != []:
                return
            self.cursor.execute("CREATE TABLE IF NOT EXISTS conf_rank (venue TEXT PRIMARY KEY, rank TEXT)")
            self.conn.commit()
        except Exception as e:
            self.conn.rollback()
            raise ValueError(f"Failed to create conf rank table: {e}")

    def insert_conf_rank_data(self, data: List[Tuple[str, str]]):
        table_name = "conf_rank"
        if len(data) == 0:
            return  
        try:
            self.cursor.executemany(f"INSERT INTO {table_name} (venue, rank) VALUES (?, ?)", data)
            self.conn.commit()
        except Exception as e:
            self.conn.rollback()
            raise ValueError(f"Failed to insert conf rank data: {e}")
    
    def get_conf_rank_data(self):
        table_name = "conf_rank"
        try:
            self.cursor.execute(f"SELECT * FROM {table_name}")
            return self.cursor.fetchall()
        except Exception as e:
            self.conn.rollback()
            raise ValueError(f"Failed to get conf rank data: {e}")
    
    def get_venue_rank_data(self, venue: str):
        table_name = "conf_rank"
        try:
            self.cursor.execute(f"SELECT rank FROM {table_name} WHERE venue = ?", (venue,))
            return self.cursor.fetchone()
        except Exception as e:
            self.conn.rollback()
            raise ValueError(f"Failed to get venue rank data: {e}")

    # -------------------------- Workflow Metadata Table Methods --------------------------

    def create_snowball_edges_table(self):
        """Create table to store snowballing provenance edges.

        Each row records that ``child_id`` was discovered while snowballing
        ``parent_id``, either as a citation (forward snowballing) or a
        reference (backward snowballing), during a given iteration. This is
        the edge list of the citation/reference graph.
        """
        table_name = "snowball_edges"
        try:
            self.cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS snowball_edges (
                    parent_id TEXT,
                    child_id TEXT,
                    direction TEXT,
                    iteration INTEGER,
                    PRIMARY KEY (parent_id, child_id, direction)
                )
                """
            )
            self.conn.commit()
        except Exception as e:
            self.conn.rollback()
            raise ValueError(f"Failed to create snowball edges table: {e}")

    def create_citation_edges_table(self):
        """Create the canonical citation-edge table.

        Each row is a directed citation edge oriented ``citing_id -> cited_id``
        (the paper ``citing_id`` cites the paper ``cited_id``). Endpoint DOIs are
        stored so edges can be matched/kept even after duplicate works are merged,
        and ``source`` records which provider reported the edge.
        """
        try:
            self.cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS citation_edges (
                    citing_id TEXT,
                    cited_id TEXT,
                    citing_doi TEXT,
                    cited_doi TEXT,
                    source TEXT,
                    iteration INTEGER,
                    PRIMARY KEY (citing_id, cited_id)
                )
                """
            )
            self.conn.commit()
        except Exception as e:
            self.conn.rollback()
            raise ValueError(f"Failed to create citation edges table: {e}")

    def insert_citation_edges(self, edges: List[Tuple]):
        """Insert canonical citation edges.

        ``edges`` is a list of ``(citing_id, cited_id, citing_doi, cited_doi,
        source, iteration)`` tuples (see :func:`build_citation_edge`). Edges missing
        an endpoint id are skipped. On conflict the row is kept but any newly
        available DOI / source is filled in, so repeated passes enrich the edge.
        """
        if not edges:
            return
        self.create_citation_edges_table()
        rows = []
        for citing_id, cited_id, citing_doi, cited_doi, source, iteration in edges:
            citing_id, cited_id = str(citing_id or ""), str(cited_id or "")
            if not citing_id or not cited_id or citing_id == cited_id:
                continue  # need two distinct endpoints for an edge
            rows.append((
                citing_id, cited_id,
                str(citing_doi or ""), str(cited_doi or ""),
                str(source or ""), int(iteration),
            ))
        if not rows:
            return
        try:
            self.cursor.executemany(
                """
                INSERT INTO citation_edges
                    (citing_id, cited_id, citing_doi, cited_doi, source, iteration)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(citing_id, cited_id) DO UPDATE SET
                    citing_doi = CASE WHEN excluded.citing_doi != '' THEN excluded.citing_doi ELSE citing_doi END,
                    cited_doi  = CASE WHEN excluded.cited_doi  != '' THEN excluded.cited_doi  ELSE cited_doi  END,
                    source     = CASE WHEN citation_edges.source = '' THEN excluded.source ELSE citation_edges.source END
                """,
                rows,
            )
            self.conn.commit()
        except Exception as e:
            self.conn.rollback()
            raise ValueError(f"Could not add citation edges: {e}")

    def _legacy_snowball_edges_as_canonical(self, iteration=None):
        """Read any pre-existing ``snowball_edges`` rows and convert them to the
        canonical ``citing -> cited`` orientation. Returns a dict keyed by
        ``(citing_id, cited_id)``. Empty when the legacy table does not exist."""
        found = self.cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='snowball_edges';"
        ).fetchall()
        if not found:
            return {}
        query = "SELECT parent_id, child_id, direction, iteration FROM snowball_edges"
        values = []
        if iteration is not None:
            query += " WHERE iteration = ?"
            values.append(int(iteration))
        self.cursor.execute(query, values)
        out = {}
        for parent_id, child_id, direction, it in self.cursor.fetchall():
            if direction == "citation":  # child cites parent
                citing, cited = child_id, parent_id
            else:  # 'reference': parent cites child
                citing, cited = parent_id, child_id
            if not citing or not cited or citing == cited:
                continue
            out[(citing, cited)] = {
                "citing_id": citing, "cited_id": cited,
                "citing_doi": "", "cited_doi": "",
                "source": "snowball", "iteration": it,
            }
        return out

    def get_citation_edges(self, **kwargs):
        """Return canonical citation edges as a list of dicts with keys
        ``citing_id, cited_id, citing_doi, cited_doi, source, iteration``.

        Optional filters: ``iteration`` and/or ``source``. Any rows from a legacy
        ``snowball_edges`` table are converted and folded in (new rows win).
        Returns an empty list if no edges are recorded.
        """
        iteration = kwargs.get("iteration")
        source = kwargs.get("source")
        merged = {}
        try:
            # Legacy edges first, so canonical rows take precedence on conflicts.
            if source in (None, "snowball"):
                merged.update(self._legacy_snowball_edges_as_canonical(iteration=iteration))

            found = self.cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='citation_edges';"
            ).fetchall()
            if found:
                conditions, values = [], []
                if iteration is not None:
                    conditions.append("iteration = ?")
                    values.append(int(iteration))
                if source is not None:
                    conditions.append("source = ?")
                    values.append(str(source))
                query = "SELECT citing_id, cited_id, citing_doi, cited_doi, source, iteration FROM citation_edges"
                if conditions:
                    query += " WHERE " + " AND ".join(conditions)
                self.cursor.execute(query, values)
                for citing_id, cited_id, citing_doi, cited_doi, src, it in self.cursor.fetchall():
                    merged[(citing_id, cited_id)] = {
                        "citing_id": citing_id, "cited_id": cited_id,
                        "citing_doi": citing_doi, "cited_doi": cited_doi,
                        "source": src, "iteration": it,
                    }
            return list(merged.values())
        except Exception as e:
            print("Error getting citation edges: ", e)
            return list(merged.values())

    def insert_snowball_edges(self, edges: List[Tuple]):
        """Backward-compatible shim: accepts legacy ``(parent_id, child_id,
        direction, iteration)`` tuples and stores them as canonical citation edges.

        Prefer :meth:`insert_citation_edges` with :func:`build_citation_edge` for
        new code (it also records endpoint DOIs and the source provider).
        """
        if not edges:
            return
        canonical = []
        for parent_id, child_id, direction, iteration in edges:
            if direction == "citation":  # child cites parent
                citing, cited = child_id, parent_id
            else:  # 'reference': parent cites child
                citing, cited = parent_id, child_id
            canonical.append((citing, cited, "", "", "snowball", iteration))
        self.insert_citation_edges(canonical)

    def get_snowball_edges(self, **kwargs):
        """Deprecated: use :meth:`get_citation_edges`. Kept for backward
        compatibility; returns canonical edges mapped onto the legacy keys
        ``parent_id``/``child_id`` (parent=citing, child=cited) with
        ``direction='reference'``."""
        direction = kwargs.get("direction")
        result = []
        for edge in self.get_citation_edges(iteration=kwargs.get("iteration")):
            if direction is not None and direction != "reference":
                continue
            result.append({
                "parent_id": edge["citing_id"],
                "child_id": edge["cited_id"],
                "direction": "reference",
                "iteration": edge["iteration"],
            })
        return result

    def create_workflow_metadata_table(self):
        """Create table to store workflow metadata (current iteration, last step, etc.)"""
        table_name = "workflow_metadata"
        try:
            tables_found = self.cursor.execute(
                f"""SELECT name FROM sqlite_master WHERE type='table' AND name='{table_name}'; """
            ).fetchall()
            if tables_found != []:
                return
            
            # Create table with a single row (key-value pair approach)
            self.cursor.execute("""
                CREATE TABLE IF NOT EXISTS workflow_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
            
            # Initialize with default values if table was just created
            self.cursor.execute("""
                INSERT OR IGNORE INTO workflow_metadata (key, value) 
                VALUES ('current_iteration', NULL), ('last_step', NULL)
            """)
            
            self.conn.commit()
        except Exception as e:
            self.conn.rollback()
            raise ValueError(f"Failed to create workflow metadata table: {e}")

    def get_workflow_metadata(self):
        """Get all workflow metadata as a dictionary"""
        table_name = "workflow_metadata"
        try:
            self.cursor.execute(f"SELECT key, value FROM {table_name}")
            rows = self.cursor.fetchall()
            metadata = {}
            for key, value in rows:
                metadata[key] = value
            return metadata
        except Exception as e:
            self.conn.rollback()
            raise ValueError(f"Failed to get workflow metadata: {e}")

    def get_current_iteration(self):
        """Get current iteration from workflow metadata"""
        try:
            metadata = self.get_workflow_metadata()
            current_iteration = metadata.get('current_iteration')
            if current_iteration is not None:
                try:
                    return int(current_iteration)
                except (ValueError, TypeError):
                    return None
            return None
        except Exception as e:
            return None

    def get_last_step(self):
        """Get last executed step from workflow metadata"""
        try:
            metadata = self.get_workflow_metadata()
            return metadata.get('last_step')
        except Exception as e:
            return None

    def set_workflow_metadata(self, key: str, value):
        """Set a workflow metadata value"""
        table_name = "workflow_metadata"
        try:
            # Convert value to string if it's not None
            if value is not None:
                value_str = str(value)
            else:
                value_str = None
            
            self.cursor.execute(
                f"INSERT OR REPLACE INTO {table_name} (key, value) VALUES (?, ?)",
                (key, value_str)
            )
            self.conn.commit()
        except Exception as e:
            self.conn.rollback()
            raise ValueError(f"Failed to set workflow metadata: {e}")

    def update_current_iteration(self, iteration):
        """Update current iteration in workflow metadata"""
        self.set_workflow_metadata('current_iteration', iteration)

    def update_last_step(self, step_name):
        """Update last executed step in workflow metadata"""
        self.set_workflow_metadata('last_step', step_name)

    def update_workflow_metadata(self, current_iteration=None, last_step=None):
        """Update workflow metadata (current iteration and/or last step)"""
        if current_iteration is not None:
            self.update_current_iteration(current_iteration)
        if last_step is not None:
            self.update_last_step(last_step)

    # -------------------------- Merge Databases Methods --------------------------
    def merge_databases(self, *other_dbs: 'DBManager'):
        """
        Merge the multiple databases into a single database.
        
        All databases have the same tables with the same structure.
        Only the screening table will contain different elements across databases.
        The resulting screening table will have all rows from all the different databases.
        
        Args:
            *other_dbs: Variable number of DBManager instances to merge into this database
        """
        table_name = "screening"
        
        try:
            # Get the schema of the current database's screening table
            self.cursor.execute(f"PRAGMA table_info({table_name})")
            current_columns = [row[1] for row in self.cursor.fetchall()]
            
            if not current_columns:
                raise ValueError(f"Screening table does not exist in the current database")
            
            for other_db in other_dbs:
                other_db.cursor.execute(f"PRAGMA table_info({table_name})")
                other_columns = [row[1] for row in other_db.cursor.fetchall()]
                
                if not other_columns:
                    continue
                
                # All columns must be present in the current database
                common_columns = [col for col in current_columns if col in other_columns]
                if not common_columns or len(common_columns) != len(current_columns):
                    raise ValueError(f"screening tables in {self.db_path} and {other_db.db_path} do not have the same columns")
                
                other_db.conn.row_factory = sqlite3.Row
                other_cursor = other_db.conn.cursor()
                other_cursor.execute(f"SELECT * FROM {table_name}")
                rows = other_cursor.fetchall()
                
                columns_str = ', '.join(common_columns)
                placeholders = ', '.join(['?'] * len(common_columns))
                insert_query = f"INSERT OR REPLACE INTO {table_name} ({columns_str}) VALUES ({placeholders})"
                
                for row in rows:
                    if isinstance(row, sqlite3.Row):
                        values = [row[col] for col in common_columns]
                    else:
                        column_to_index = {col: i for i, col in enumerate(other_columns)}
                        values = [row[column_to_index[col]] if col in column_to_index else None for col in common_columns]
                    
                    self.cursor.execute(insert_query, values)
                
                other_db.conn.row_factory = None
                other_cursor.close()
            
            self.conn.commit()
        except Exception as e:
            self.conn.rollback()
            raise ValueError(f"Failed to merge databases: {e}")
        
def initialize_db(db_path: str, search_conf: dict):
    db_manager = DBManager(db_path, new_db=True)
    annotations = search_conf.get("annotations", [])
    db_manager.create_iterations_table(annotations=annotations)
    db_manager.create_seen_titles_table()
    db_manager.create_conf_rank_table()
    db_manager.create_workflow_metadata_table()
    db_manager.create_screening_table(annotations)
    db_manager.create_annotations_table(annotations)
    db_manager.create_snowball_edges_table()
    db_manager.create_citation_edges_table()
    return db_manager

def get_iteration_setup(db_path: str, **kwargs):
    db_manager = DBManager(db_path)
    return db_manager, db_manager.get_iteration_data(**kwargs)

def merge_databases(db_paths: list[str]):
    """
    Merge the multiple databases into a single database.
    """
    merged_db = DBManager(db_paths[0])
    db_managers = [DBManager(db_path) for db_path in db_paths[1:]]
    merged_db.merge_databases(*db_managers)
    return merged_db

    