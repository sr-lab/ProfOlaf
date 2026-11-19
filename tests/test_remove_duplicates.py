"""Tests for utils/pipeline/remove_duplicates.py."""
from conftest import make_article
from utils.db_management import SelectionStage
from utils.pipeline import remove_duplicates as rd


class TestTitleSimilarity:
    def test_identical_titles(self):
        assert rd.calculate_title_similarity("Deep Learning", "Deep Learning") == 1.0

    def test_case_and_whitespace_insensitive(self):
        assert rd.calculate_title_similarity("Deep Learning", "  deep learning ") == 1.0

    def test_dissimilar_titles(self):
        assert rd.calculate_title_similarity("Cats", "Quantum Chromodynamics") < 0.3


class TestFindDuplicateCandidates:
    def test_detects_near_duplicate(self):
        arts = [
            make_article("A", title="A Survey of Code LLMs"),
            make_article("B", title="A Survey of Code LLMs."),  # trailing dot
        ]
        pairs = rd.find_duplicate_candidates(arts, 0.9)
        assert len(pairs) == 1
        assert {pairs[0][0].id, pairs[0][1].id} == {"A", "B"}

    def test_ignores_dissimilar(self):
        arts = [
            make_article("A", title="A Survey of Code LLMs"),
            make_article("B", title="Formal Verification of Compilers"),
        ]
        assert rd.find_duplicate_candidates(arts, 0.9) == []


class TestAutoRemove:
    def _setup_pair(self, db, cit_a, cit_b):
        db.insert_iteration_data([
            make_article("A", iteration=1, title="Neural Code Search", num_citations=cit_a),
            make_article("B", iteration=1, title="Neural Code Search", num_citations=cit_b),
        ])

    def _duplicate_ids(self, db):
        rows = db.get_iteration_data(iteration=1)
        return {r.id for r in rows if str(r.selected) == str(SelectionStage.DUPLICATE.value)}

    def test_keeps_higher_citation_count(self, db):
        """Regression: num_citations is stored as TEXT and read back as a string,
        so the previous `>` comparison was lexicographic ("100" < "9"). The article
        with more citations (100) must be kept, and the 9-citation one removed."""
        self._setup_pair(db, 100, 9)
        rd.remove_duplicates(db, [1], similarity_threshold=0.9, auto_remove=True)
        assert self._duplicate_ids(db) == {"B"}

    def test_keeps_higher_citation_count_reversed(self, db):
        self._setup_pair(db, 9, 100)
        rd.remove_duplicates(db, [1], similarity_threshold=0.9, auto_remove=True)
        assert self._duplicate_ids(db) == {"A"}

    def test_tie_breaks_on_year(self, db):
        db.insert_iteration_data([
            make_article("A", iteration=1, title="Static Analysis", num_citations=5, pub_year=2023),
            make_article("B", iteration=1, title="Static Analysis", num_citations=5, pub_year=2019),
        ])
        rd.remove_duplicates(db, [1], similarity_threshold=0.9, auto_remove=True)
        # equal citations -> keep the newer (2023); remove the older (2019)
        assert self._duplicate_ids(db) == {"B"}

    def test_no_duplicates_leaves_db_untouched(self, db):
        db.insert_iteration_data([
            make_article("A", iteration=1, title="Quantum Error Correction"),
            make_article("B", iteration=1, title="Build Systems at Scale"),
        ])
        rd.remove_duplicates(db, [1], similarity_threshold=0.9, auto_remove=True)
        assert self._duplicate_ids(db) == set()
