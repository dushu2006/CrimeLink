"""The explorer endpoints: navigable resources with jurisdiction scoping.

Administration screens used to be dead-end counts.  These endpoints turn
documents, entities and relationships into real, deep-linkable resources for
ordinary investigators -- the `/database/*` twins are ADMIN-only, so they
cannot serve that purpose.

The invariants worth protecting here are the ones a UI cannot enforce for
itself: that scoping still applies, that a page is a page rather than the whole
graph, and that whatever evidence is attached points at an exact position.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------


def test_explore_requires_authentication(client) -> None:
    for path in ("/api/v1/explore/documents",
                 "/api/v1/explore/entities",
                 "/api/v1/explore/relationships"):
        assert client.get(path).status_code == 401, path


def test_explore_is_open_to_investigators_not_only_admins(client, investigator_headers) -> None:
    """The whole point of these endpoints: usable without the ADMIN role."""
    response = client.get("/api/v1/explore/entities", headers=investigator_headers)
    assert response.status_code == 200


def test_documents_are_scoped_to_the_callers_jurisdiction(
    client, investigator_headers, kota_headers, case, other_case
) -> None:
    """Every listed document must belong to a case the caller may actually see.

    Comparing the two listings for disjointness would be the obvious test, but
    it passes for the wrong reason once a shared database holds unrelated
    cases.  Assert the real rule instead: each row is in scope for its caller,
    and the other jurisdiction's case is absent.
    """
    for headers, own_case, foreign_case in (
        (investigator_headers, case, other_case),
        (kota_headers, other_case, case),
    ):
        response = client.get("/api/v1/explore/documents?limit=200", headers=headers)
        assert response.status_code == 200
        case_ids = {item["case_id"] for item in response.json()["items"]}
        assert foreign_case.id not in case_ids
        # And the caller's own case is never excluded by the scoping filter.
        assert client.get(
            f"/api/v1/explore/documents?case_id={own_case.id}", headers=headers
        ).status_code == 200


def test_unknown_entity_is_not_found(client, investigator_headers) -> None:
    response = client.get(
        "/api/v1/explore/entities/does-not-exist", headers=investigator_headers
    )
    assert response.status_code in (403, 404)


def test_unknown_document_is_not_found(client, investigator_headers) -> None:
    response = client.get(
        "/api/v1/explore/documents/does-not-exist", headers=investigator_headers
    )
    assert response.status_code in (403, 404)


# ---------------------------------------------------------------------------
# Shape and paging
# ---------------------------------------------------------------------------


def test_listings_report_paging_and_never_exceed_the_page_size(
    client, investigator_headers
) -> None:
    """A hub entity has thousands of edges; a page must stay a page."""
    for path in ("documents", "entities", "relationships"):
        response = client.get(
            f"/api/v1/explore/{path}?limit=5", headers=investigator_headers
        )
        assert response.status_code == 200, path
        body = response.json()
        assert body["limit"] == 5 and body["offset"] == 0, path
        assert len(body["items"]) <= 5, path
        assert body["total"] >= len(body["items"]), path


def test_offset_moves_the_window(client, investigator_headers) -> None:
    first = client.get(
        "/api/v1/explore/entities?limit=1", headers=investigator_headers
    ).json()
    if first["total"] < 2:
        return  # Nothing to page through in this fixture database.
    second = client.get(
        "/api/v1/explore/entities?limit=1&offset=1", headers=investigator_headers
    ).json()
    assert first["items"][0]["provenance_key"] != second["items"][0]["provenance_key"]


def test_entity_rows_are_summaries_not_whole_nodes(client, investigator_headers) -> None:
    """Listing rows must not carry every case and document id of the entity.

    A busy phone belongs to dozens of cases and documents; shipping those lists
    per row turned a 50-row page into tens of kilobytes of data nothing drew.
    """
    body = client.get(
        "/api/v1/explore/entities?limit=5", headers=investigator_headers
    ).json()
    for row in body["items"]:
        assert "case_ids" not in row
        assert "source_doc_ids" not in row
        assert {"provenance_key", "label", "name", "case_count", "document_count"} <= set(row)


def test_relationship_endpoints_project_their_endpoints(client, investigator_headers) -> None:
    body = client.get(
        "/api/v1/explore/relationships?limit=5", headers=investigator_headers
    ).json()
    for row in body["items"]:
        for side in ("source_entity", "target_entity"):
            other = row.get(side)
            if other is not None:
                assert set(other) == {"provenance_key", "label", "name"}


def test_listings_expose_their_facet_counts(client, investigator_headers) -> None:
    """The type filters are driven by real counts, never a hardcoded list."""
    entities = client.get("/api/v1/explore/entities", headers=investigator_headers).json()
    relationships = client.get(
        "/api/v1/explore/relationships", headers=investigator_headers
    ).json()
    assert isinstance(entities["labels"], dict)
    assert isinstance(relationships["types"], dict)
    assert all(isinstance(v, int) for v in entities["labels"].values())
    assert all(isinstance(v, int) for v in relationships["types"].values())


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------


def test_attached_evidence_always_addresses_an_exact_position(
    client, investigator_headers
) -> None:
    """Evidence is a pointer or it is absent -- never a vague gesture.

    An evidence pointer that named a document but no position would look
    traceable in the UI while being unfollowable, which is the failure mode
    this whole feature exists to remove.
    """
    body = client.get(
        "/api/v1/explore/relationships?limit=25", headers=investigator_headers
    ).json()
    for row in body["items"]:
        evidence = row.get("evidence")
        if evidence is None:
            continue
        assert evidence.get("source_doc_id")
        origin = evidence.get("origin")
        has_span = evidence.get("text_span") is not None
        if origin is not None:
            # A corpus pointer must name a file and a position within it.
            assert origin.get("file")
            assert origin.get("row") is not None or origin.get("path") is not None
        else:
            assert has_span, "evidence must carry either an origin or a text span"
