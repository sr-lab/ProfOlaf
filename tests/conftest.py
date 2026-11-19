"""Shared pytest fixtures for the ProfOlaf backend test suite.

Everything here builds *fresh, throwaway* SQLite databases in a tmp dir through
the real ``DBManager`` API, so tests exercise production code against the real
schema (including annotation columns) without ever touching the databases under
``databases/``.
"""
import os
import sys

import pytest

# Make the project root importable regardless of where pytest is launched from.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from utils.db_management import (  # noqa: E402
    ArticleData,
    DBManager,
    SelectionStage,
    initialize_db,
)

# A representative set of annotation columns, matching the shape of the real
# merged_content_screening.db ("Scope", "Techniques", ...).
DEFAULT_ANNOTATIONS = ["Scope", "Techniques"]


@pytest.fixture
def annotations():
    return list(DEFAULT_ANNOTATIONS)


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "mock.db")


@pytest.fixture
def db(db_path, annotations):
    """A freshly initialized DBManager with all tables created."""
    manager = initialize_db(db_path, {"annotations": annotations})
    yield manager
    try:
        manager.conn.close()
    except Exception:
        pass


@pytest.fixture
def make_db(tmp_path, annotations):
    """Factory to create additional independent mock databases (e.g. for merge tests)."""
    created = []

    def _make(name="extra.db", annots=None):
        path = str(tmp_path / name)
        manager = initialize_db(path, {"annotations": annots if annots is not None else annotations})
        created.append(manager)
        return manager

    yield _make
    for m in created:
        try:
            m.conn.close()
        except Exception:
            pass


def make_article(
    article_id,
    *,
    title=None,
    iteration=0,
    selected=SelectionStage.NOT_SELECTED,
    venue="Some Conference",
    pub_year=2022,
    search_method="semantic_scholar",
    new_pub=False,
    **overrides,
):
    """Build an ArticleData the way the pipeline does, with sensible defaults."""
    art = ArticleData(
        id=str(article_id),
        title=title if title is not None else f"Article {article_id}",
        venue=venue,
        pub_year=pub_year,
        iteration=iteration,
        selected=selected,
        new_pub=new_pub,
        search_method=search_method,
    )
    for key, value in overrides.items():
        setattr(art, key, value)
    return art


@pytest.fixture
def sample_articles():
    """Factory producing a list of ArticleData for a given iteration."""

    def _make(iteration=0, count=3, start=1, **kw):
        return [
            make_article(start + i, iteration=iteration, **kw)
            for i in range(count)
        ]

    return _make
