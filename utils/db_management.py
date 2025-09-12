import sqlite3
import json
from collections import defaultdict
from dataclasses import dataclass, asdict, fields
from typing import List, Tuple
from enum import Enum

# Enum for the different selection stages of the process
class SelectionStage(Enum):
    NOT_SELECTED = 0
    METADATA_APPROVED = 1
    TITLE_APPROVED = 2
    ABSTRACT_INTRO_APPROVED = 3


def get_article_data(pub, pub_id, iteration: int, selected: SelectionStage = SelectionStage.NOT_SELECTED, new_pub: bool = False):
    """
    Get the article data from the pub.
    """
    print(pub)
    pub_info = {}
    pub_info["id"] = pub_id
    pub_info["container_type"] = pub["container_type"]
    pub_info["eprint_url"] = pub["pub_url"] if "eprint_url" not in pub else pub["eprint_url"]
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
    return ArticleData(**pub_info)

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
    year_filtered_out: bool = False
    venue_filtered_out: bool = False
    title_filtered_out: bool = False
    abstract_filtered_out: bool = False
    language_filtered_out: bool = False
    download_filtered_out: bool = False
    new_pub: bool = False
    selected: bool = False
    bibtex: str = ""
    iteration: int = 0
    dict = asdict
    
    def __hash__(self):
        # Use id as the primary hash since it should be unique
        return hash(self.id)
    
    def __eq__(self, other):
        if not isinstance(other, ArticleData):
            return False
        return self.id == other.id



class DBManager:
    SQL_TYPES = {
        str: 'TEXT',
        int: 'TEXT',  # Changed from INTEGER to TEXT to handle large integers
        float: 'REAL',
        bool: 'BOOLEAN'
    }
    def __init__(self, db_path: str):
        self.conn = sqlite3.connect(db_path)
        self.cursor = self.conn.cursor()

    # -------------------------- Iteration Table Methods --------------------------

    def create_iterations_table(self):
        # create a table for the iteration if it doesn't exist
        table_name = "iterations"
        try:
            tables_found = self.cursor.execute(
                f"""SELECT name FROM sqlite_master WHERE type='table' AND name='{table_name}'; """
            ).fetchall()

            if tables_found != []:
                return
            
            field_definitions = []
            for field in fields(ArticleData):
                field_name = field.name
                field_type = field.type
                if field_type not in self.SQL_TYPES:
                    raise ValueError(f"Unsupported field type: {field_type}")
                sql_type = self.SQL_TYPES[field_type]
                field_definitions.append(f"{field_name} {sql_type}")
                
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
        try:
            
            data_dicts = [data_element.__dict__ for data_element in data]
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
        """Get iteration data as dictionaries with field names as keys."""
        table_name = "iterations"
        try:
            self.conn.row_factory = sqlite3.Row
            if kwargs:
                conditions = ' AND '.join([f"{key} = ?" for key in kwargs.keys()])
                sql_query = f"SELECT * FROM {table_name} WHERE {conditions}"
                # Convert enum values to their underlying values for SQLite
                values = []
                for key in kwargs:
                    value = kwargs[key]
                    if hasattr(value, 'value'):  # Check if it's an enum
                        values.append(value.value)
                    else:
                        values.append(value)
                print(sql_query, values)
                self.cursor.execute(sql_query, values)
            else:
                self.cursor.execute(f"SELECT * FROM {table_name}")
            
            rows = self.cursor.fetchall()
            dict_list = []
            for row in rows:
                row_dict = {}
                for i, field in enumerate(fields(ArticleData)):
                    if i < len(row):
                        row_dict[field.name] = row[i]
                dict_list.append(ArticleData(**row_dict))
            
            return dict_list
        except Exception as e:
            print("Error getting iteration data: ", e)
            self.conn.rollback()
            raise ValueError(f"Failed to get iteration data: {e}")
        finally:
            self.conn.row_factory = None
    
    def update_iteration_data(self, iteration: int, article_id: str, **kwargs):
        table_name = "iterations"
        try:
            columns = ', '.join(kwargs.keys())
            placeholders = ', '.join(['?'] * len(kwargs))
            sql_query = f"UPDATE {table_name} SET {columns} = {placeholders} WHERE id = ? and iteration = ?"
            for key, value in kwargs.items():
                if hasattr(value, 'value'):  # Handle enum values
                    kwargs[key] = value.value
                elif isinstance(value, (list, dict)):
                    kwargs[key] = json.dumps(value)
                elif value is None:
                    kwargs[key] = ""
                elif isinstance(value, int) and key in ['id', 'pub_year', 'num_citations']:  # Convert large integers to strings
                    kwargs[key] = str(value)
            self.cursor.execute(sql_query, [kwargs[key] for key in kwargs] + [article_id, iteration])
            self.conn.commit()
        except Exception as e:
            self.conn.rollback()
            raise ValueError(f"Failed to update iteration data: {e}")

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


def initialize_db(db_path: str):
    db_manager = DBManager(db_path)
    db_manager.create_iterations_table()
    db_manager.create_seen_titles_table()
    db_manager.create_conf_rank_table()
    return db_manager
