"""Tests for utils/article_search/article_search_method.py.

The HTTP boundary (requests / the _openalex_get helper) is mocked, so no network
is used. Covers the SearchMethod registry, venue validation, the Semantic Scholar
and OpenAlex mappers, OpenAlex search/snowball/bibtex, and the DBLP repair.
"""
import pytest
import requests

from utils.article_search import article_search_method as asm
from utils.db_management import ArticleData


class FakeResp:
    def __init__(self, json_data=None, status=200, text=""):
        self._json = json_data or {}
        self.status_code = status
        self.text = text
        self.headers = {}

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"status {self.status_code}", response=self)


class TestSearchMethodRegistry:
    @pytest.mark.parametrize("value,clsname", [
        ("google_scholar", "GoogleScholarSearchMethod"),
        ("semantic_scholar", "SemanticScholarSearchMethod"),
        ("dblp", "DBLPSearchMethod"),
        ("openalex", "OpenAlexSearchMethod"),
        ("crossref", "CrossrefSearchMethod"),
    ])
    def test_create_instance(self, value, clsname):
        assert asm.SearchMethod(value).create_instance().__class__.__name__ == clsname


class TestCheckValidVenue:
    @pytest.mark.parametrize("venue", ["ICSE", "Transactions on Software Engineering"])
    def test_valid(self, venue):
        assert asm.check_valid_venue(venue) is True

    @pytest.mark.parametrize("venue", ["", "arXiv", "arXiv e-prints", "CoRR abs/2001.1", "No Title"])
    def test_invalid(self, venue):
        assert asm.check_valid_venue(venue) is False


class TestSemanticScholarMapping:
    def test_reads_open_access_pdf(self):
        """Regression: the key was misspelled 'openAcessPdf', so the OA PDF URL
        was never used."""
        m = asm.SemanticScholarSearchMethod()
        pub = m.map_to_pub({
            "title": "T", "venue": "ICSE", "year": 2022, "paperId": "abc",
            "authors": [], "openAccessPdf": {"url": "https://x.org/p.pdf"}, "citationCount": 5,
        })
        assert pub["eprint_url"] == "https://x.org/p.pdf"
        assert pub["num_citations"] == 5

    def test_handles_missing_fields(self):
        """Regression: map_to_pub indexed article['venue']/['year']/['title'] directly
        and raised KeyError when Semantic Scholar omitted them."""
        m = asm.SemanticScholarSearchMethod()
        pub = m.map_to_pub({"paperId": "abc"})
        assert pub["paperId"] == "abc" and pub["venue"] == "" and pub["title"] == ""


class TestOpenAlex:
    WORK = {
        "id": "https://openalex.org/W42",
        "title": "Deep Code Search",
        "publication_year": 2021,
        "primary_location": {"source": {"display_name": "ICSE"}},
        "authorships": [{"author": {"display_name": "A. Author"}}],
        "open_access": {"oa_url": "https://x.org/p.pdf"},
        "doi": "https://doi.org/10.1145/abcd",
        "cited_by_count": 7,
        "referenced_works": ["https://openalex.org/W1", "https://openalex.org/W2"],
    }

    def test_map_to_pub(self):
        pub = asm.OpenAlexSearchMethod().map_to_pub(self.WORK)
        assert pub["paperId"] == "W42"
        assert pub["title"] == "Deep Code Search"
        assert pub["venue"] == "ICSE"
        assert str(pub["pub_year"]) == "2021"
        assert pub["authors"] == "A. Author"
        assert pub["eprint_url"] == "https://x.org/p.pdf"
        assert pub["num_citations"] == 7

    def test_map_to_pub_empty(self):
        assert asm.OpenAlexSearchMethod().map_to_pub({})["paperId"] == ""

    def test_captures_doi_and_abstract(self):
        m = asm.OpenAlexSearchMethod()
        work = dict(self.WORK, abstract_inverted_index={"Hello": [0], "world": [1]})
        pub = m.map_to_pub(work)
        art = m.get_article_data(pub, pub["paperId"], search_method="openalex")
        assert art.doi == "10.1145/abcd"  # normalized (prefix stripped, lowercased)
        assert art.abstract == "Hello world"

    def test_search(self, monkeypatch):
        monkeypatch.setattr(asm, "_openalex_get", lambda url, params=None, timeout=60: FakeResp({"results": [self.WORK]}))
        art = asm.OpenAlexSearchMethod().search("deep code search")
        assert art.id == "W42" and art.venue == "ICSE" and art.search_method == "openalex"

    def test_search_no_results(self, monkeypatch):
        monkeypatch.setattr(asm, "_openalex_get", lambda url, params=None, timeout=60: FakeResp({"results": []}))
        assert asm.OpenAlexSearchMethod().search("nothing") is None

    def test_snowball_forward_and_backward(self, monkeypatch):
        m = asm.OpenAlexSearchMethod()
        citing = [{"id": "https://openalex.org/W100", "title": "Citing Paper"}]
        refs = [{"id": "https://openalex.org/W200", "title": "Referenced Paper"}]
        monkeypatch.setattr(m, "_get_citing_works", lambda oid: citing)
        monkeypatch.setattr(m, "_get_referenced_works", lambda oid: refs)
        res = m.get_snowballing_articles("W42", backwards=True, forwards=True, iteration=1)
        dirs = {(a.id, d) for a, d in res}
        assert ("W100", "citation") in dirs  # forward snowball (papers citing the seed)
        assert ("W200", "reference") in dirs  # backward snowball (seed's references)

    def test_snowball_none_requested(self, monkeypatch):
        m = asm.OpenAlexSearchMethod()
        monkeypatch.setattr(m, "_get_citing_works", lambda oid: [{"id": "x"}])
        assert m.get_snowballing_articles("W42") == []  # neither direction requested

    def test_get_bibtex_via_doi(self, monkeypatch):
        m = asm.OpenAlexSearchMethod()
        monkeypatch.setattr(asm, "_openalex_get", lambda url, params=None, timeout=60: FakeResp({"results": [{"doi": "https://doi.org/10.1/x"}]}))
        monkeypatch.setattr(asm, "fetch_bibtex_from_doi", lambda doi, timeout=60: "@inproceedings{x, title={T}}")
        bib = m.get_bibtex(ArticleData(id="W42", title="Deep Code Search"))
        assert bib.startswith("@inproceedings")

    def test_get_bibtex_no_doi(self, monkeypatch):
        m = asm.OpenAlexSearchMethod()
        monkeypatch.setattr(asm, "_openalex_get", lambda url, params=None, timeout=60: FakeResp({"results": [{"doi": ""}]}))
        assert m.get_bibtex(ArticleData(id="W42", title="X")) is None


class TestCrossref:
    WORK = {
        "DOI": "10.1145/abcd",
        "title": ["Deep Code Search"],
        "container-title": ["ICSE"],
        "issued": {"date-parts": [[2021, 5]]},
        "author": [{"given": "A.", "family": "Author"}],
        "is-referenced-by-count": 12,
        "URL": "https://doi.org/10.1145/abcd",
    }

    def test_map_to_pub(self):
        pub = asm.CrossrefSearchMethod().map_to_pub(self.WORK)
        assert pub["paperId"] == "10.1145/abcd"
        assert pub["title"] == "Deep Code Search"
        assert pub["venue"] == "ICSE"
        assert str(pub["pub_year"]) == "2021"
        assert pub["authors"] == "A. Author"
        assert pub["num_citations"] == 12

    def test_map_to_pub_no_doi_has_empty_id(self):
        assert asm.CrossrefSearchMethod().map_to_pub({"title": ["X"]})["paperId"] == ""

    def test_search(self, monkeypatch):
        monkeypatch.setattr(asm, "_crossref_get", lambda url, params=None, timeout=60: FakeResp({"message": {"items": [self.WORK]}}))
        art = asm.CrossrefSearchMethod().search("deep code search")
        assert art.id == "10.1145/abcd" and art.venue == "ICSE" and art.search_method == "crossref"

    def test_search_no_results(self, monkeypatch):
        monkeypatch.setattr(asm, "_crossref_get", lambda url, params=None, timeout=60: FakeResp({"message": {"items": []}}))
        assert asm.CrossrefSearchMethod().search("nothing") is None

    def test_backward_snowball_from_references(self, monkeypatch):
        m = asm.CrossrefSearchMethod()
        refs = [
            {"DOI": "10.1/ref1", "article-title": "Referenced One", "journal-title": "TSE", "year": "2018"},
            {"unstructured": "A reference with no DOI"},  # skipped (no stable id)
        ]
        monkeypatch.setattr(asm, "_crossref_get", lambda url, params=None, timeout=60: FakeResp({"message": {"reference": refs}}))
        res = m.get_snowballing_articles("10.1145/abcd", forwards=True, iteration=1)
        assert [(a.id, d) for a, d in res] == [("10.1/ref1", "reference")]

    def test_forward_snowball_unsupported(self, monkeypatch):
        m = asm.CrossrefSearchMethod()
        # backwards kwarg = citations = forward snowball, which Crossref cannot do
        assert m.get_snowballing_articles("10.1145/abcd", backwards=True) == []

    def test_get_bibtex_uses_article_doi(self, monkeypatch):
        m = asm.CrossrefSearchMethod()
        monkeypatch.setattr(asm, "fetch_bibtex_from_doi", lambda doi, timeout=60: f"@article{{{doi}}}")
        bib = m.get_bibtex(ArticleData(id="10.1/x", title="T", search_method="crossref"))
        assert bib == "@article{10.1/x}"


class TestFetchBibtexFromDoi:
    def test_success(self, monkeypatch):
        monkeypatch.setattr(asm.requests, "get", lambda url, headers=None, timeout=60: FakeResp(status=200, text="@article{x, title={T}}"))
        assert asm.fetch_bibtex_from_doi("10.1/x").startswith("@article")

    def test_non_bibtex_response(self, monkeypatch):
        monkeypatch.setattr(asm.requests, "get", lambda url, headers=None, timeout=60: FakeResp(status=200, text="<html>not bibtex</html>"))
        assert asm.fetch_bibtex_from_doi("10.1/x") is None

    def test_empty_doi(self):
        assert asm.fetch_bibtex_from_doi("") is None


class TestDBLP:
    def test_search_returns_articledata(self, monkeypatch):
        """Regression: search() passed key= to ArticleData (no such field) -> TypeError
        whenever a valid venue was found."""
        hit = {"info": {"title": "A DBLP Paper", "venue": "ICSE", "year": "2021", "key": "conf/icse/X"}}
        data = {"result": {"hits": {"hit": [hit]}}}
        monkeypatch.setattr(asm.requests, "get", lambda url, headers=None, timeout=60: FakeResp(data))
        art = asm.DBLPSearchMethod().search("a dblp paper")
        assert art is not None
        assert art.title == "A DBLP Paper" and art.venue == "ICSE" and art.search_method == "dblp"

    def test_search_skips_preprint_venue(self, monkeypatch):
        hit = {"info": {"title": "Preprint", "venue": "CoRR", "year": "2021", "key": "journals/corr/X"}}
        data = {"result": {"hits": {"hit": [hit]}}}
        monkeypatch.setattr(asm.requests, "get", lambda url, headers=None, timeout=60: FakeResp(data))
        assert asm.DBLPSearchMethod().search("preprint") is None


class TestNormalizeDoi:
    @pytest.mark.parametrize("raw,expected", [
        ("https://doi.org/10.1145/ABC", "10.1145/abc"),
        ("doi:10.1/x", "10.1/x"),
        ("10.1/Y", "10.1/y"),
        ("", ""),
    ])
    def test_normalize(self, raw, expected):
        assert asm.normalize_doi(raw) == expected
