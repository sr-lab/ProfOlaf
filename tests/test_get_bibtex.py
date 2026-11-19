"""Tests for the pure bibtex-parsing logic in utils/pipeline/get_bibtex.py.

Network calls (Google Scholar / DBLP / Semantic Scholar) are not exercised.
"""
import pytest

from utils.pipeline import get_bibtex as gb

INPROC = "@inproceedings{k, title={T}, booktitle={ICSE}, year={2022}}"
JOURNAL = "@article{k, title={T}, journal={TSE}, year={2021}}"
BOOK = "@book{k, title={T}, publisher={Springer}, year={2020}}"


class TestCheckValidVenue:
    @pytest.mark.parametrize("venue", ["ICSE", "Transactions on Software Engineering", "Information and Computation"])
    def test_valid(self, venue):
        assert gb.check_valid_venue(venue) is True

    @pytest.mark.parametrize("venue", ["", "arXiv", "arXiv e-prints", "CoRR abs/2001.00001", "CoRR", "No Title"])
    def test_invalid(self, venue):
        """Regression: the old `venue.lower() not in keyword` check only rejected an
        exact "arxiv"/"corr"; multi-word preprint venues slipped through."""
        assert gb.check_valid_venue(venue) is False


class TestParseBibtex:
    def test_empty_returns_empty_string(self):
        """Regression: used to return None implicitly, crashing downstream getters."""
        assert gb.parse_bibtex("") == ""

    def test_garbage_returns_empty_string(self):
        assert gb.parse_bibtex("not bibtex at all") == ""

    def test_valid_returns_entry(self):
        entry = gb.parse_bibtex(INPROC)
        assert entry["ENTRYTYPE"] == "inproceedings" and entry["booktitle"] == "ICSE"


class TestVenueAndYearExtraction:
    def test_venue_from_booktitle(self):
        assert gb.get_bibtex_venue(INPROC) == "ICSE"

    def test_venue_from_journal(self):
        assert gb.get_bibtex_venue(JOURNAL) == "TSE"

    def test_venue_empty_for_book(self):
        assert gb.get_bibtex_venue(BOOK) == ""

    def test_venue_empty_for_empty_bibtex(self):
        """Regression: get_bibtex_venue("") crashed (None['ENTRYTYPE'])."""
        assert gb.get_bibtex_venue("") == ""

    def test_year(self):
        assert gb.get_bibtex_year(INPROC) == "2022"

    def test_year_empty_for_empty_bibtex(self):
        """Regression: get_bibtex_year("") crashed ("year" in None)."""
        assert gb.get_bibtex_year("") == ""


class TestGetBibtexSingleOpenAlex:
    def test_openalex_method_uses_openalex_then_returns_bibtex(self, monkeypatch):
        from utils.article_search.article_search_method import OpenAlexSearchMethod, SearchMethod
        from utils.db_management import ArticleData
        monkeypatch.setattr(OpenAlexSearchMethod, "get_bibtex", lambda self, article: "@inproceedings{x, title={T}, booktitle={ICSE}}")
        aid, bibtex = gb.get_bibtex_single(
            ArticleData(id="W1", title="A Paper", search_method="openalex"),
            search_method=SearchMethod.OPENALEX.value,
            delay_between_requests=0,
        )
        assert aid == "W1" and bibtex.startswith("@inproceedings")


class TestUpdateBibtexInfo:
    def test_handles_empty_bibtex_without_crashing(self, db):
        """A batch item that timed out comes through as ("id", "", "") and must not
        crash update_bibtex_info. The parallel path passes "" as the 3rd tuple
        element, which previously became an (empty) column name -> SQL syntax error."""
        from conftest import make_article
        db.insert_iteration_data([
            make_article("A", iteration=1),
            make_article("B", iteration=1),
        ])
        results = [
            ("A", INPROC, ""),   # good bibtex (parallel-path shape: 3rd elem "")
            ("B", "", ""),       # failed download -> empty bibtex
        ]
        gb.update_bibtex_info(1, results, db)
        rows = {r.id: r for r in db.get_iteration_data(iteration=1)}
        assert rows["A"].venue == "ICSE" and rows["A"].pub_year == "2022"
        assert rows["A"].bibtex == INPROC  # bibtex column actually written
        assert rows["B"].venue == "" and rows["B"].pub_year == ""
