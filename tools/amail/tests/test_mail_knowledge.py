import sqlite3

import pytest

import amail.mail_knowledge as fs


class TestInitDb:
    def test_creates_table(self, temp_db):
        conn = fs._get_conn()
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='facts'"
        ).fetchall()
        conn.close()
        assert len(tables) == 1

    def test_idempotent(self, temp_db):
        fs.init_db()
        fs.init_db()
        conn = fs._get_conn()
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='facts'"
        ).fetchall()
        conn.close()
        assert len(tables) == 1


class TestSaveAndListFacts:
    def test_save_and_retrieve(self, temp_db, sample_facts):
        fs.save_facts(sample_facts, source_subject="Test Subject", source_sender="a@b.com")
        all_facts = fs.list_all_facts()
        assert len(all_facts) == 3
        assert all_facts[0]["project"] == "ARCO"
        assert all_facts[0]["source_subject"] == "Test Subject"
        assert all_facts[0]["source_sender"] == "a@b.com"

    def test_empty_save_does_not_crash(self, temp_db):
        fs.save_facts([], source_subject="Empty", source_sender="")
        all_facts = fs.list_all_facts()
        assert all_facts == []

    def test_list_all_returns_most_recent_first(self, temp_db):
        fs.save_facts(
            [{"project": "A", "topic": "t", "detail": "first"}],
            source_subject="S1", source_sender="x"
        )
        fs.save_facts(
            [{"project": "B", "topic": "t", "detail": "second"}],
            source_subject="S2", source_sender="y"
        )
        all_facts = fs.list_all_facts()
        assert all_facts[0]["detail"] == "second"
        assert all_facts[1]["detail"] == "first"


class TestSearchFacts:
    def test_exact_match(self, temp_db, sample_facts):
        fs.save_facts(sample_facts, source_subject="S", source_sender="x")
        results = fs.search_facts(
            subject="concrete pour schedule",
            content="Need details on slab pour",
            category="",
            limit=5,
        )
        assert len(results) >= 1
        assert any("concrete" in r["topic"].lower() for r in results)

    def test_no_match_returns_empty(self, temp_db):
        results = fs.search_facts(
            subject="xyzzy nonexit",
            content="blarg",
            category="",
            limit=5,
        )
        assert results == []

    def test_empty_query_returns_empty(self, temp_db):
        results = fs.search_facts(subject="", content="", category="", limit=5)
        assert results == []

    def test_stop_words_are_filtered(self, temp_db, sample_facts):
        """Stop words like 'the', 'and', 'for' should be excluded from FTS query."""
        fs.save_facts(sample_facts, source_subject="S", source_sender="x")
        results = fs.search_facts(
            subject="the and for have been with this that",
            content="",
            category="",
            limit=5,
        )
        # All words are stop words, so query should be empty → no results
        assert results == []

    def test_short_words_filtered(self, temp_db, sample_facts):
        """Words <= 2 chars should be excluded."""
        fs.save_facts(sample_facts, source_subject="S", source_sender="x")
        results = fs.search_facts(subject="a ab 12 xy", content="", category="", limit=5)
        assert results == []

    def test_category_field_searched(self, temp_db, sample_facts):
        fs.save_facts(sample_facts, source_subject="S", source_sender="x")
        results = fs.search_facts(
            subject="", content="", category="concrete pour", limit=5
        )
        assert len(results) >= 1
        assert any("concrete" in r["topic"].lower() for r in results)

    def test_limit_respected(self, temp_db, sample_facts):
        fs.save_facts(sample_facts, source_subject="S", source_sender="x")
        results = fs.search_facts(
            subject="ARCO project", content="slab window change", category="", limit=2
        )
        assert len(results) <= 2

    def test_special_characters_in_query(self, temp_db, sample_facts):
        """Apostrophes, hyphens, periods in query should not cause FTS5 syntax errors."""
        fs.save_facts(sample_facts, source_subject="S", source_sender="x")
        # This should not raise OperationalError
        results = fs.search_facts(
            subject="O'Brien's project",
            content="pre-cast panel. Check ref. no.",
            category="",
            limit=5,
        )
        assert isinstance(results, list)

    def test_special_characters_in_data(self, temp_db):
        """Facts with apostrophes should be stored and searchable."""
        facts = [{"project": "O'Brien", "topic": "spec", "detail": "Owner's requirement"}]
        fs.save_facts(facts, source_subject="S", source_sender="x")
        results = fs.search_facts(subject="O'Brien", content="", category="", limit=5)
        assert len(results) >= 1

    def test_deduplication_in_query(self, temp_db, sample_facts):
        """Repeated words in the query should not appear multiple times in FTS query."""
        fs.save_facts(sample_facts, source_subject="S", source_sender="x")
        results = fs.search_facts(
            subject="ARCO ARCO ARCO project project",
            content="",
            category="",
            limit=5,
        )
        assert len(results) >= 1
