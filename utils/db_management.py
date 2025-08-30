import sqlite3
import json
from dataclasses import dataclass, asdict, fields
from typing import List, Tuple

def get_article_data(pub, pub_id, new_pub: bool = False):
    pub_info = {}
    pub_info["id"] = pub_id
    pub_info["container_type"] = pub["container_type"]
    pub_info["source"] = pub["source"]
    pub_info["title"] = pub["bib"]["title"]
    pub_info["authors"] = pub["bib"]["author"]
    pub_info["venue"] = pub["bib"]["venue"]
    pub_info["pub_year"] = pub["bib"]["pub_year"]
    pub_info["pub_url"] = pub["pub_url"]
    pub_info["num_citations"] = pub["num_citations"]
    pub_info["citedby_url"] = pub.get("citedby_url", "")
    pub_info["url_related_articles"] = pub.get("url_related_articles", "")
    pub_info["new_pub"] = new_pub
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
    year_filtered_out: bool = False
    venue_filtered_out: bool = False
    title_filtered_out: bool = False
    abstract_filtered_out: bool = False
    language_filtered_out: bool = False
    new_pub: bool = False
    bibtex: str = ""
    dict = asdict

class DBManager:
    SQL_TYPES = {
        str: 'TEXT',
        int: 'INTEGER',
        float: 'REAL',
        bool: 'BOOLEAN'
    }
    def __init__(self, db_path: str):
        self.conn = sqlite3.connect(db_path)
        self.cursor = self.conn.cursor()

    def create_iteration_table(self, iteration: int):
        # create a table for the iteration if it doesn't exist
        table_name = f"iteration_{iteration}"
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
            field_definitions.append(f"{field_name} {self.SQL_TYPES[field_type]}")
        self.cursor.execute(f"CREATE TABLE IF NOT EXISTS {table_name} ({', '.join(field_definitions)})")
        self.conn.commit()
    
    def create_seen_titles_table(self):
        table_name = "seen_titles"
        tables_found = self.cursor.execute(
            f"""SELECT name FROM sqlite_master WHERE type='table' AND name='{table_name}'; """
        ).fetchall()
        if tables_found != []:
            return
        self.cursor.execute("CREATE TABLE IF NOT EXISTS seen_titles (title TEXT PRIMARY KEY, id TEXT)")
        self.conn.commit()

    def create_conf_rank_table(self):
        table_name = "conf_rank"
        tables_found = self.cursor.execute(
            f"""SELECT name FROM sqlite_master WHERE type='table' AND name='{table_name}'; """
        ).fetchall()
        if tables_found != []:
            return
        self.cursor.execute("CREATE TABLE IF NOT EXISTS conf_rank (venue TEXT PRIMARY KEY, rank TEXT)")
        self.conn.commit()

    def insert_iteration_data(self, iteration: int, data: List[ArticleData]):
        table_name = f"iteration_{iteration}"
        data_dicts = [data_element.__dict__ for data_element in data]
        
        for data_dict in data_dicts:
            for key, value in data_dict.items():
                if not isinstance(value, (list, dict)):
                    continue
                data_dict[key] = json.dumps(value)
        
        columns = ', '.join(data_dicts[0].keys())
        placeholders = ', '.join(['?'] * len(data_dicts[0]))
        sql_query = f"INSERT INTO {table_name} ({columns}) VALUES ({placeholders})"
        try:
            self.cursor.executemany(sql_query, [tuple(data_dict.values()) for data_dict in data_dicts])
            self.conn.commit()
        except Exception as e:
            raise ValueError(f"Could not add elements to table: {e}")
    
    def insert_seen_titles_data(self, data: List[Tuple[str, str]]):
        # data is a list of tuples (title, id)
        table_name = "seen_titles"
        self.cursor.executemany(f"INSERT INTO {table_name} (title, id) VALUES (?, ?)", data)
        self.conn.commit()

    def insert_conf_rank_data(self, data: List[Tuple[str, str]]):
        table_name = "conf_rank"
        self.cursor.executemany(f"INSERT INTO {table_name} (venue, rank) VALUES (?, ?)", data)
        self.conn.commit()

    def get_iteration_data(self, iteration: int):
        table_name = f"iteration_{iteration}"
        self.cursor.execute(f"SELECT * FROM {table_name}")
        return self.cursor.fetchall()
    
    def get_seen_titles_data(self):
        table_name = "seen_titles"
        self.cursor.execute(f"SELECT * FROM {table_name}")
        return self.cursor.fetchall()
    
    def get_conf_rank_data(self):
        table_name = "conf_rank"
        self.cursor.execute(f"SELECT * FROM {table_name}")
        return self.cursor.fetchall()
    
    def update_iteration_data(self, iteration: int, article_id: str, **kwargs):
        table_name = f"iteration_{iteration}"
        columns = ', '.join(kwargs.keys())
        placeholders = ', '.join(['?'] * len(kwargs))
        sql_query = f"UPDATE {table_name} SET {columns} = {placeholders} WHERE id = ?"
        for key, value in kwargs.items():
            if not isinstance(value, (list, dict)):
                continue
            kwargs[key] = json.dumps(value)
        self.cursor.execute(sql_query, [kwargs[key] for key in kwargs] + [article_id])
        self.conn.commit()

def initialize_db(iteration: int):
    db_manager = DBManager(f"prof_olaf.db")
    db_manager.create_iteration_table(iteration)
    db_manager.create_seen_titles_table()
    db_manager.create_conf_rank_table()
    return db_manager
