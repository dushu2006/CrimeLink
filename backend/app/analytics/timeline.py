"""Chronological event reconstruction (PRD 10 — ``GET /cases/{id}/timeline``).

Events are first-class graph nodes, which is what makes "who was present at
this event" and "what happened at this location within this window" answerable
at all.  The reconstruction is shared by every graph backend so a timeline
looks identical whether the case lives in Neo4j or in the embedded store.
"""

from __future__ import annotations

from typing import Any

from app.domain.models import CaseGraphSnapshot


def build_timeline(
    snapshot: CaseGraphSnapshot,
    *,
    from_ts: str | None = None,
    to_ts: str | None = None,
    participant: str | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Flatten a case snapshot into an ordered, evidence-annotated event list."""
    participants_by_event: dict[str, list[dict[str, Any]]] = {}
    location_by_event: dict[str, str] = {}

    for edge in snapshot.edges:
        if edge.rel_type == "PARTICIPATED_IN":
            person = snapshot.nodes.get(edge.source_key)
            event = snapshot.nodes.get(edge.target_key)
            if person is None or event is None:
                continue
            participants_by_event.setdefault(event.provenance_key, []).append(
                {
                    "provenance_key": person.provenance_key,
                    "name": person.name,
                    "role": edge.properties.get("role"),
                    "evidence_doc_ids": list(edge.properties.get("source_doc_ids") or []),
                }
            )
        elif edge.rel_type == "LOCATED_AT":
            event = snapshot.nodes.get(edge.source_key)
            location = snapshot.nodes.get(edge.target_key)
            if event is not None and location is not None:
                location_by_event[event.provenance_key] = str(
                    location.properties.get("address") or location.name
                )

    events: list[dict[str, Any]] = []
    for key, node in snapshot.nodes.items():
        if node.label != "Event":
            continue
        docs = list(node.properties.get("source_doc_ids") or [])
        events.append(
            {
                "event_key": key,
                "event_type": node.properties.get("event_type"),
                "timestamp": node.properties.get("timestamp"),
                "description": node.properties.get("description"),
                "location": location_by_event.get(key) or node.properties.get("address"),
                "participants": participants_by_event.get(key, []),
                "source_doc_id": node.properties.get("source_doc_id") or (docs[0] if docs else None),
                "evidence_doc_ids": docs,
                "confidence": float(node.properties.get("confidence", 1.0) or 1.0),
            }
        )

    events.sort(key=lambda entry: str(entry.get("timestamp") or ""))
    if from_ts:
        events = [e for e in events if str(e.get("timestamp") or "") >= from_ts]
    if to_ts:
        events = [e for e in events if str(e.get("timestamp") or "") <= to_ts]
    if participant:
        needle = participant.lower()
        events = [
            e
            for e in events
            if any(needle in str(p.get("name", "")).lower() for p in e["participants"])
        ]
    return events[:limit]
