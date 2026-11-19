"""Tests for generate_snowball_start_utils and generate_conf_rank_utils
(pure helpers + orchestration with the search boundary faked)."""
from utils.db_management import ArticleData, SelectionStage
from utils.pipeline import generate_snowball_start_utils as gss
from utils.pipeline import generate_conf_rank_utils as gcr


class TestExtractTitles:
    def test_strips_and_skips_blank_lines(self, tmp_path):
        p = tmp_path / "seed.txt"
        p.write_text("Title One\n\n   Title Two  \n\n")
        assert gss.extract_titles_from_file(str(p)) == ["Title One", "Title Two"]


class TestGenerateSnowballStart:
    def test_marks_seeds_as_content_approved(self, tmp_path, monkeypatch):
        p = tmp_path / "seed.txt"
        p.write_text("Paper A\nPaper B\n")

        class FakeSearch:
            def __init__(self, method):
                pass

            def search(self, title):
                return ArticleData(id="id-" + title.replace(" ", ""), title=title)

        monkeypatch.setattr(gss, "ArticleSearch", FakeSearch)
        pubs, seen = gss.generate_snowball_start(
            str(p), iteration=0, delay=0, search_method=gss.SearchMethod.OPENALEX
        )
        assert len(pubs) == 2
        assert all(a.iteration == 0 for a in pubs)
        assert all(a.selected == SelectionStage.CONTENT_APPROVED for a in pubs)
        assert {t for t, _id in seen} == {"Paper A", "Paper B"}

    def test_search_failures_are_skipped(self, tmp_path, monkeypatch):
        p = tmp_path / "seed.txt"
        p.write_text("Good\nBad\n")

        class FakeSearch:
            def __init__(self, method):
                pass

            def search(self, title):
                if title == "Bad":
                    raise RuntimeError("boom")
                return ArticleData(id="good", title=title)

        monkeypatch.setattr(gss, "ArticleSearch", FakeSearch)
        pubs, seen = gss.generate_snowball_start(
            str(p), iteration=1, delay=0, search_method=gss.SearchMethod.OPENALEX
        )
        assert [a.id for a in pubs] == ["good"]


class TestGetVenues:
    def test_extracts_conf_and_journal_excludes_books_and_empty(self):
        arts = [
            ArticleData(id="1", bibtex="@inproceedings{x, title={T}, booktitle={ICSE}}"),
            ArticleData(id="2", bibtex="@article{y, title={T}, journal={TSE}}"),
            ArticleData(id="3", bibtex="@book{z, title={T}, publisher={Springer}}"),
            ArticleData(id="4", bibtex=""),
            ArticleData(id="5", bibtex="NO_BIBTEX"),
        ]
        assert gcr.get_venues(arts) == {"ICSE", "TSE"}


class TestFindSimilarVenues:
    def test_finds_textually_similar_venue(self):
        existing = {
            "International Conference on Software Engineering",
            "Journal of Systems and Software",
        }
        conf_rank = {"International Conference on Software Engineering": "A*"}
        res = gcr.find_similar_venues(
            "Conference on Software Engineering", existing, conf_rank, threshold=0.1
        )
        assert res, "expected at least one similar venue"
        assert res[0][0] == "International Conference on Software Engineering"
        assert res[0][2] == "A*"  # rank is carried through

    def test_empty_existing_returns_empty(self):
        assert gcr.find_similar_venues("Anything", set(), {}) == []
