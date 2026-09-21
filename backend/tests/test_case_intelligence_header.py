"""Regression: the CASE INTELLIGENCE header metrics are the authoritative ones.

The Case Evidence Assistant header reads ``GET /ai/cases/{id}/context``.  For
CR-2020 the assistant's own case-scoped context reported 12 documents, 11
evidence types, 10 people, 65 relationships and a dated timeline, while the
header above it showed ``0 evidence types · 0 entities · 0 case-scoped
relationships · First recorded: N/A``.

Root cause: ``build_case_ai_context`` assigned ``document_count`` on a
``frozen`` ``CaseContextStats`` and raised ``FrozenInstanceError``.  The
endpoint failed on every call, the console fell back to ``GET /cases/{id}``
(which only knows the document count) and painted every other metric as
``0``/``N/A``.  No test exercised the builder or the endpoint, so the failure
was invisible to the suite.

These tests pin the contract from both ends: the builder on a CR-2020-shaped
case and a second, differently shaped case; the genuinely empty case; the
case-scope boundary; and the endpoint itself.
"""

from __future__ import annotations

import dataclasses

import pytest

from app.ai.case_context import (
    CaseContextStats,
    build_case_ai_context,
    format_readable_date,
)
from app.domain.models import GraphEdge, GraphNode

# --------------------------------------------------------------------------- #
# Case fixtures shaped exactly like the gateway's case-scoped snapshot
# --------------------------------------------------------------------------- #

CR_2020_EVIDENCE_TYPES = [
    "FIR", "CDR", "FINANCIAL", "ANPR", "WITNESS_STATEMENT", "FORENSIC",
    "SURVEILLANCE", "CASE_DIARY", "CHARGE_SHEET", "ARREST_RECORD", "BAIL_RECORD",
]


def _case_fixture(
    case_id: str,
    case_number: str,
    *,
    people: int,
    phones: int,
    accounts: int,
    documents: int,
    evidence_types: list[str],
    relationships: int,
    first_ts: str,
    last_ts: str,
) -> dict:
    """One self-contained case in the shape ``_get_all_case_nodes``,
    ``_get_all_case_edges`` and ``_retrieve_case_documents`` produce."""
    nodes: list[dict] = []
    for i in range(people):
        nodes.append({
            "provenance_key": f"{case_id}:person:{i}",
            "label": "Person",
            "properties": {"name": f"Person {i} of {case_number}", "case_ids": [case_id]},
            "confidence": 1.0,
        })
    for i in range(phones):
        nodes.append({
            "provenance_key": f"{case_id}:phone:{i}",
            "label": "Phone",
            "properties": {"number": f"+91-98{i:08d}", "case_ids": [case_id]},
            "confidence": 1.0,
        })
    for i in range(accounts):
        nodes.append({
            "provenance_key": f"{case_id}:account:{i}",
            "label": "BankAccount",
            "properties": {"account_number": f"5010{i:010d}", "case_ids": [case_id]},
            "confidence": 1.0,
        })
    # An Event node dates the case, as the demo corpus does.
    nodes.append({
        "provenance_key": f"{case_id}:event:0",
        "label": "Event",
        "properties": {"name": "Incident", "timestamp": first_ts, "case_ids": [case_id]},
        "confidence": 1.0,
    })

    docs = [
        {
            "doc_id": f"{case_number}-DOC-{i:02d}",
            "case_id": case_id,
            "filename": f"{case_number}_{evidence_types[i % len(evidence_types)]}_{i:02d}.txt",
            "document_type": evidence_types[i % len(evidence_types)],
            "content": f"Record {i} of {case_number}.",
        }
        for i in range(documents)
    ]

    keys = [n["provenance_key"] for n in nodes if n["label"] != "Event"]
    edges: list[dict] = []
    for i in range(relationships):
        source = keys[i % len(keys)]
        target = keys[(i * 7 + 1) % len(keys)]
        if source == target:
            target = keys[(i * 7 + 2) % len(keys)]
        edges.append({
            "source_key": source,
            "target_key": target,
            "rel_type": "CALLED" if i % 2 else "ASSOCIATE_OF",
            "confidence": 0.9,
            "timestamp": last_ts if i == relationships - 1 else None,
            "source_doc_ids": [docs[i % len(docs)]["doc_id"]],
            "case_ids": [case_id],
        })
    return {
        "case_id": case_id,
        "case_number": case_number,
        "nodes": nodes,
        "edges": edges,
        "documents": docs,
        "first_ts": first_ts,
        "last_ts": last_ts,
    }


CR_2020 = _case_fixture(
    "case-d2-019", "CR-2020",
    people=10, phones=8, accounts=6,
    documents=12, evidence_types=CR_2020_EVIDENCE_TYPES, relationships=65,
    first_ts="2026-01-12T09:15:00+00:00", last_ts="2026-03-08T18:40:00+00:00",
)
CR_2001 = _case_fixture(
    "case-d2-000", "CR-2001",
    people=4, phones=2, accounts=2,
    documents=5, evidence_types=["FIR", "CDR", "FINANCIAL", "ANPR", "WITNESS_STATEMENT"],
    relationships=14,
    first_ts="2024-08-01T08:00:00+00:00", last_ts="2024-08-28T20:30:00+00:00",
)


def _build(case: dict, *, extra_nodes=(), extra_edges=(), extra_documents=()):
    from app.ai.retrieval import build_timeline_from_context

    nodes = list(case["nodes"]) + list(extra_nodes)
    edges = list(case["edges"]) + list(extra_edges)
    documents = list(case["documents"]) + list(extra_documents)
    return build_case_ai_context(
        case_id=case["case_id"],
        case_number=case["case_number"],
        case_title=f"{case['case_number']} investigation",
        nodes=nodes,
        edges=edges,
        documents=documents,
        events=build_timeline_from_context(nodes, edges),
    )


# --------------------------------------------------------------------------- #
# 1. The builder must not raise, and must report the case's own figures
# --------------------------------------------------------------------------- #


def test_case_context_stats_stays_frozen():
    """The stats are deliberately immutable; the fix respects that."""
    stats = CaseContextStats(
        case_id="c", evidence_count=1, entity_count=1, person_count=1, relationship_count=1
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        stats.document_count = 5  # type: ignore[misc]


def test_cr_2020_header_metrics_are_authoritative_and_non_zero():
    summary = _build(CR_2020).as_summary_dict()
    stats = summary["stats"]

    assert stats["documents_indexed"] == 12
    assert stats["document_count"] == 12
    assert stats["evidence_types_count"] == 11
    assert sorted(stats["evidence_types"]) == sorted(CR_2020_EVIDENCE_TYPES)
    assert stats["entity_counts_by_type"]["people"] == 10
    assert stats["person_count"] == 10
    assert stats["entities_extracted"] == 10 + 8 + 6 + 1  # people, phones, accounts, event
    assert stats["relationships_mapped"] == 65
    assert summary["timeline"]["first_recorded"] == format_readable_date(CR_2020["first_ts"])
    assert summary["timeline"]["latest_recorded"] == format_readable_date(CR_2020["last_ts"])
    assert summary["timeline"]["event_count"] > 0

    # The regression's exact rendering must be impossible from this payload.
    assert stats["evidence_types_count"] != 0
    assert stats["entities_extracted"] != 0
    assert stats["relationships_mapped"] != 0
    assert summary["timeline"]["first_recorded"] != "N/A"
    assert summary["timeline"]["latest_recorded"] != "N/A"


def test_a_second_case_reports_its_own_non_zero_metrics():
    summary = _build(CR_2001).as_summary_dict()
    stats = summary["stats"]
    assert stats["documents_indexed"] == 5
    assert stats["evidence_types_count"] == 5
    assert stats["entity_counts_by_type"] == {"people": 4, "phones": 2, "accounts": 2, "event": 1}
    assert stats["relationships_mapped"] == 14
    assert summary["timeline"]["first_recorded"] == "Aug 01, 2024"
    assert summary["timeline"]["latest_recorded"] == "Aug 28, 2024"

    # Nothing is hardcoded: two cases, two different headers.
    other = _build(CR_2020).as_summary_dict()
    assert other["stats"] != stats
    assert other["timeline"] != summary["timeline"]


def test_the_header_figures_match_the_assistants_case_scoped_context():
    """The header is derived from the very lists the assistant reasons over."""
    context = _build(CR_2020)
    stats = context.stats
    assert stats.document_count == len(context.verified_evidence) == 12
    assert stats.entity_count == len(context.entities)
    assert stats.relationship_count == len(context.relationships) == 65
    assert stats.evidence_types_count == len(
        {d["document_type"] for d in context.verified_evidence}
    )
    assert stats.person_count == sum(1 for n in context.entities if n["label"] == "Person") == 10


# --------------------------------------------------------------------------- #
# 2. A genuinely empty case reads zero / N/A
# --------------------------------------------------------------------------- #


def test_an_empty_case_reports_zeros_and_na():
    summary = build_case_ai_context(
        case_id="case-empty", case_number="CR-0000", case_title="Newly registered case"
    ).as_summary_dict()
    stats = summary["stats"]
    assert stats["documents_indexed"] == 0
    assert stats["evidence_types_count"] == 0
    assert stats["evidence_types"] == []
    assert stats["entities_extracted"] == 0
    assert stats["entity_counts_by_type"] == {}
    assert stats["relationships_mapped"] == 0
    assert summary["timeline"] == {
        "first_recorded": "N/A", "latest_recorded": "N/A", "event_count": 0
    }


def test_a_populated_case_without_dated_records_reads_na_for_the_timeline():
    undated = dict(CR_2001)
    undated["nodes"] = [n for n in CR_2001["nodes"] if n["label"] != "Event"]
    undated["edges"] = [{**e, "timestamp": None} for e in CR_2001["edges"]]
    summary = _build(undated).as_summary_dict()
    assert summary["stats"]["relationships_mapped"] == 14
    assert summary["timeline"]["first_recorded"] == "N/A"
    assert summary["timeline"]["latest_recorded"] == "N/A"
    assert summary["timeline"]["event_count"] == 0


# --------------------------------------------------------------------------- #
# 3. Case scope: nothing from another case enters the header
# --------------------------------------------------------------------------- #


def test_cross_case_records_never_enter_the_header_metrics():
    baseline = _build(CR_2020).as_summary_dict()
    foreign_nodes = [dict(n) for n in CR_2001["nodes"]]
    foreign_edges = [dict(e) for e in CR_2001["edges"]]
    foreign_docs = [dict(d) for d in CR_2001["documents"]]
    # An edge from a CR-2020 person to a CR-2001 person, stamped for CR-2020:
    # the endpoint outside the case context disqualifies it.
    bridge = {
        "source_key": CR_2020["nodes"][0]["provenance_key"],
        "target_key": CR_2001["nodes"][0]["provenance_key"],
        "rel_type": "CALLED",
        "case_ids": [CR_2020["case_id"]],
        "source_doc_ids": [CR_2020["documents"][0]["doc_id"]],
        "timestamp": "2019-01-01T00:00:00+00:00",
    }
    # An unscoped legacy edge between two CR-2020 people: no provenance, no count.
    unscoped = {
        "source_key": CR_2020["nodes"][0]["provenance_key"],
        "target_key": CR_2020["nodes"][1]["provenance_key"],
        "rel_type": "ASSOCIATE_OF",
        "source_doc_ids": [CR_2020["documents"][0]["doc_id"]],
        "timestamp": "2010-01-01T00:00:00+00:00",
    }
    mixed = _build(
        CR_2020,
        extra_nodes=foreign_nodes,
        extra_edges=foreign_edges + [bridge, unscoped],
        extra_documents=foreign_docs,
    ).as_summary_dict()

    assert mixed["stats"] == baseline["stats"]
    assert mixed["timeline"] == baseline["timeline"]
    assert mixed["timeline"]["first_recorded"] == "Jan 12, 2026"  # not the bridge's 2019 stamp


# --------------------------------------------------------------------------- #
# 4. Timeline bounds are chronological, whatever the stamp format
# --------------------------------------------------------------------------- #


def test_timeline_bounds_are_ordered_chronologically_not_lexically():
    case_id = "case-mixed"
    scope = {"case_ids": [case_id]}
    nodes = [
        {"provenance_key": "n1", "label": "Person",
         "properties": {"name": "A", "first_ts": "2025-06-30T23:30:00Z", **scope}},
        {"provenance_key": "n2", "label": "Person",
         "properties": {"name": "B", "last_ts": "2025-07-01T02:00:00+05:30", **scope}},
        {"provenance_key": "n3", "label": "Event",
         "properties": {"name": "E", "timestamp": "2025-07-01", **scope}},
    ]
    edges = [
        {"source_key": "n1", "target_key": "n2", "rel_type": "CALLED",
         "timestamp": "2025-06-30 23:59:00", **scope},
        {"source_key": "n2", "target_key": "n1", "rel_type": "CALLED",
         "timestamp": "Timestamp unavailable", **scope},
    ]
    summary = build_case_ai_context(
        case_id=case_id, case_number="CR-MIX", case_title="Mixed stamps", nodes=nodes, edges=edges
    ).as_summary_dict()
    # 2025-07-01T02:00+05:30 is 2025-06-30T20:30Z -- the earliest instant, even
    # though it sorts last as a string.
    assert summary["timeline_summary"]["first_recorded_raw"] == "2025-07-01T02:00:00+05:30"
    assert summary["timeline_summary"]["latest_recorded_raw"] == "2025-07-01"
    assert summary["timeline"]["first_recorded"] == "Jul 01, 2025"
    assert summary["timeline"]["event_count"] == 4  # the unavailable stamp is not an event


# --------------------------------------------------------------------------- #
# 5. The endpoint the header calls, end to end
# --------------------------------------------------------------------------- #


async def _seed_case(db, graph, case, *, people: int, doc_types: list, dated: bool = True) -> dict:
    """Store documents for ``case`` and project its people/relationships into
    the graph, the way the demo seed and dataset projection do."""
    from app.db.models import CaseDocument

    doc_ids: list[str] = []
    for i, doc_type in enumerate(doc_types):
        doc_id = f"doc-{case.id[:8]}-{i:02d}"
        doc_ids.append(doc_id)
        db.add(
            CaseDocument(
                id=doc_id,
                case_id=case.id,
                document_type=doc_type,
                filename=f"{case.case_number}_{doc_type.value}_{i:02d}.txt",
                storage_key=f"evidence/{case.case_number}/{i:02d}.txt",
                content_hash=f"{i:064x}",
                size_bytes=10,
            )
        )
    await db.commit()

    keys = [f"person-{case.id[:8]}-{i}" for i in range(people)]
    graph.upsert_nodes(
        [
            GraphNode(
                provenance_key=key,
                label="Person",
                properties={
                    "name": f"Person {i} ({case.case_number})",
                    "case_ids": [case.id],
                    "source_doc_id": doc_ids[0],
                    "source_doc_ids": [doc_ids[0]],
                },
            )
            for i, key in enumerate(keys)
        ]
    )
    edges = []
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            stamp = f"2024-08-{(i + j) % 27 + 1:02d}T10:00:00+00:00"
            edges.append(
                GraphEdge(
                    source_key=keys[i],
                    target_key=keys[j],
                    rel_type="ASSOCIATE_OF",
                    key=f"assoc-{keys[i]}-{keys[j]}",
                    properties={
                        "case_id": case.id,
                        "case_ids": [case.id],
                        "source_doc_id": doc_ids[0],
                        "source_doc_ids": [doc_ids[0]],
                        "confidence": 0.9,
                        **({"timestamp": stamp} if dated else {}),
                    },
                )
            )
    graph.upsert_edges(edges)
    return {"doc_ids": doc_ids, "keys": keys, "edges": len(edges)}


@pytest.mark.asyncio
async def test_context_endpoint_returns_authoritative_non_zero_metrics(
    client, investigator_headers, case, graph, db
):
    from app.domain.enums import DocumentType

    seeded = await _seed_case(
        db, graph, case, people=5,
        doc_types=[DocumentType.FIR, DocumentType.CDR, DocumentType.FINANCIAL, DocumentType.ANPR],
    )

    response = client.get(f"/api/v1/ai/cases/{case.id}/context", headers=investigator_headers)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["case_number"] == case.case_number
    assert body["stats"]["documents_indexed"] == 4
    assert body["stats"]["evidence_types_count"] == 4
    assert sorted(body["stats"]["evidence_types"]) == ["ANPR", "CDR", "FINANCIAL", "FIR"]
    assert body["stats"]["entities_extracted"] == 5
    assert body["stats"]["entity_counts_by_type"] == {"people": 5}
    assert body["stats"]["relationships_mapped"] == seeded["edges"] == 10
    assert body["timeline"]["first_recorded"] == "Aug 02, 2024"
    assert body["timeline"]["latest_recorded"] == "Aug 08, 2024"
    # Each dated relationship is one event -- not two (once via the timeline
    # events, once via the direct relationship scan).
    assert body["timeline"]["event_count"] == 10

    # The header and the assistant read the same case-scoped snapshot: the
    # figures must equal what the gateway itself retrieves for this case.
    from app.ai.gateway import get_ai_gateway

    ai_gateway = get_ai_gateway()
    documents = await ai_gateway._retrieve_case_documents(case.id)
    nodes = await ai_gateway._get_all_case_nodes(case.id)
    edges = await ai_gateway._get_all_case_edges(case.id)
    assert len(documents) == body["stats"]["documents_indexed"]
    assert len(nodes) == body["stats"]["entities_extracted"]
    assert len(edges) == body["stats"]["relationships_mapped"]


@pytest.mark.asyncio
async def test_context_endpoint_keeps_two_cases_apart(
    client, investigator_headers, case, graph, db, users
):
    from app.domain.enums import DocumentType
    from tests.conftest import _make_case

    second = await _make_case(db, users["INV-0001"], "FIR/2024/0002/PS-TEST", "RJ-JAIPUR")
    await _seed_case(db, graph, case, people=3, doc_types=[DocumentType.FIR, DocumentType.CDR])
    await _seed_case(
        db, graph, second, people=6,
        doc_types=[DocumentType.FIR, DocumentType.FINANCIAL, DocumentType.WITNESS_STATEMENT],
    )

    first = client.get(f"/api/v1/ai/cases/{case.id}/context", headers=investigator_headers).json()
    other = client.get(f"/api/v1/ai/cases/{second.id}/context", headers=investigator_headers).json()

    assert first["stats"]["documents_indexed"] == 2
    assert first["stats"]["entities_extracted"] == 3
    assert first["stats"]["relationships_mapped"] == 3
    assert other["stats"]["documents_indexed"] == 3
    assert other["stats"]["entities_extracted"] == 6
    assert other["stats"]["relationships_mapped"] == 15
    assert first["stats"]["evidence_types"] != other["stats"]["evidence_types"]


@pytest.mark.asyncio
async def test_context_endpoint_reports_an_empty_case_as_zero_and_na(
    client, investigator_headers, case
):
    response = client.get(f"/api/v1/ai/cases/{case.id}/context", headers=investigator_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["stats"]["documents_indexed"] == 0
    assert body["stats"]["evidence_types_count"] == 0
    assert body["stats"]["entities_extracted"] == 0
    assert body["stats"]["relationships_mapped"] == 0
    assert body["timeline"]["first_recorded"] == "N/A"
    assert body["timeline"]["latest_recorded"] == "N/A"


@pytest.mark.asyncio
async def test_context_endpoint_respects_jurisdiction_scope(client, case):
    # A dedicated out-of-jurisdiction investigator: the suite's access-grant
    # tests lawfully open RJ-JAIPUR to INV-0002 for a while, so relying on
    # the Kota fixture here would depend on test order.
    from tests.conftest import _make_user, auth_headers

    _make_user("INV-0099", "Inspector Udaipur", "INVESTIGATOR", "RJ-UDAIPUR")
    headers = auth_headers(client, "INV-0099")
    response = client.get(f"/api/v1/ai/cases/{case.id}/context", headers=headers)
    assert response.status_code in (403, 404), response.text
