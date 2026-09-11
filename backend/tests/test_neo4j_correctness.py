"""Regression tests for Neo4j Cypher correctness (WS 1.1 and 1.2).

These test the *generated* Cypher, not a live Neo4j server.  A live
server test belongs in Workstream 3.
"""

from __future__ import annotations

import re

import pytest

from app.adapters.graph.neo4j import CONSTRAINTS, Neo4jGraphStore, _PK_LABELS, _FULLTEXT_LABELS
from app.domain.enums import EntityType


# ---- WS 1.1: expand query contains literal integer, not $depth -----------

class TestExpandQueryDepthLiteral:
    """The generated expand query must embed the depth as a literal integer."""

    @pytest.fixture()
    def store(self):
        """A Neo4jGraphStore with the driver disabled (Cypher-only tests)."""
        # Patch out GraphDatabase so we don't need a real connection
        import app.adapters.graph.neo4j as mod
        original = mod.GraphDatabase
        mod.GraphDatabase = None
        yield None
        mod.GraphDatabase = original

    def test_expand_query_depth_1(self):
        """Depth 1 should produce *1..1 in the query."""
        # Directly test the method by constructing a minimal instance
        store = object.__new__(Neo4jGraphStore)
        store._expand_templates = {}
        query = store._expand_query(1)
        assert "*1..1" in query
        assert "$depth" not in query

    def test_expand_query_depth_2(self):
        store = object.__new__(Neo4jGraphStore)
        store._expand_templates = {}
        query = store._expand_query(2)
        assert "*1..2" in query
        assert "$depth" not in query

    def test_expand_query_depth_3(self):
        store = object.__new__(Neo4jGraphStore)
        store._expand_templates = {}
        query = store._expand_query(3)
        assert "*1..3" in query
        assert "$depth" not in query

    def test_expand_query_caches(self):
        """Repeated calls for the same depth return the cached string."""
        store = object.__new__(Neo4jGraphStore)
        store._expand_templates = {}
        q1 = store._expand_query(2)
        q2 = store._expand_query(2)
        assert q1 is q2

    def test_expand_query_different_depths(self):
        """Different depths produce different cached queries."""
        store = object.__new__(Neo4jGraphStore)
        store._expand_templates = {}
        q1 = store._expand_query(1)
        q2 = store._expand_query(3)
        assert q1 != q2
        assert "*1..1" in q1
        assert "*1..3" in q2


# ---- WS 1.2: constraints have proper label scope -------------------------

class TestConstraintLabelScope:
    """Every generated constraint must be scoped to a specific label."""

    def test_no_labelless_for_n(self):
        """No constraint statement should contain 'FOR (n)' without a label."""
        for stmt in CONSTRAINTS:
            # Match FOR (x) but NOT FOR (x:Label)
            if re.search(r"FOR\s*\(\w+\)\s+REQUIRE", stmt):
                pytest.fail(
                    f"Constraint uses label-less FOR (n): {stmt}"
                )

    def test_pk_constraints_cover_all_entity_types(self):
        """There should be one pk_unique constraint per EntityType + Case."""
        pk_stmts = [s for s in CONSTRAINTS if "pk_unique" in s]
        labels_in_constraints = set()
        for stmt in pk_stmts:
            match = re.search(r"FOR\s*\(\w+:(\w+)\)", stmt)
            assert match, f"pk_unique constraint has no label: {stmt}"
            labels_in_constraints.add(match.group(1))
        expected = {e.value for e in EntityType} | {"Case"}
        assert labels_in_constraints == expected

    def test_fulltext_index_has_explicit_labels(self):
        """The fulltext index must list explicit labels, not bare (n)."""
        ft_stmts = [s for s in CONSTRAINTS if "FULLTEXT INDEX" in s]
        assert len(ft_stmts) == 1, "Expected exactly one fulltext index"
        stmt = ft_stmts[0]
        assert "FOR (n)" not in stmt or "FOR (n:" in stmt
        # Verify it contains at least Person and Phone
        assert "Person" in stmt
        assert "Phone" in stmt

    def test_fulltext_index_excludes_event(self):
        """Event nodes don't carry name/aliases/search_text."""
        ft_stmts = [s for s in CONSTRAINTS if "FULLTEXT INDEX" in s]
        stmt = ft_stmts[0]
        # The label list shouldn't include Event
        label_match = re.search(r"FOR\s*\(n:([\w|]+)\)", stmt)
        assert label_match
        labels = label_match.group(1).split("|")
        assert "Event" not in labels

    def test_domain_constraints_preserved(self):
        """Phone, Vehicle, BankAccount, Case domain constraints still exist."""
        constraint_text = "\n".join(CONSTRAINTS)
        assert "phone_uniq" in constraint_text
        assert "plate_uniq" in constraint_text
        assert "acct_uniq" in constraint_text
        assert "case_uniq" in constraint_text
