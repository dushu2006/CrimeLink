"""Deterministic pattern detection (PRD 11.3).

All four rules are **deterministic**, not ML classifiers, because a rule is
auditable, explainable and unit-testable — and because a false accusation is
far more costly than a missed heuristic.

Every rule returns its supporting entity keys, the documents that evidence it,
and a plain-language ``explanation`` string.  Findings are written with status
``NEW`` and are **never** presented to an investigator as a confirmed finding.

The rules operate on :class:`~app.domain.models.CaseGraphSnapshot`, which both
graph backends produce.  That gives the rules exactly one implementation — the
same code path runs in CI against the embedded graph and in production against
Neo4j — which is what makes them genuinely auditable instead of auditable in
theory.  (Equivalent Cypher for GDS-scale deployments is maintained in
``infra/neo4j/patterns.cypher`` for reference.)
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable

from app.config import Settings, get_settings
from app.domain.enums import PatternType
from app.domain.models import CaseGraphSnapshot
from app.logging import get_logger

log = get_logger("crimelink.analytics.patterns")


@dataclass(slots=True)
class PatternFinding:
    """A candidate finding awaiting human review — never a confirmed fact."""

    pattern_type: PatternType
    confidence: float
    entity_keys: list[str]
    evidence_doc_ids: list[str]
    explanation: str
    details: dict[str, Any] = field(default_factory=dict)

    def dedupe_key(self) -> tuple:
        return (self.pattern_type.value, tuple(sorted(self.entity_keys)))


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres."""
    radius = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


class PatternEngine:
    """Runs the four deterministic rules over a case subgraph."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    # ------------------------------------------------------------------ API
    def detect_event_triggered(
        self,
        snapshot: CaseGraphSnapshot,
        *,
        changed_doc_ids: Iterable[str] | None = None,
        thresholds: dict[str, float] | None = None,
    ) -> list[PatternFinding]:
        """Cheap local checks run on every injection commit.

        Only the newly injected documents are considered, which keeps the check
        proportional to the size of the new data rather than the whole case.
        """
        cfg = self._config(thresholds)
        changed = set(changed_doc_ids or [])
        scoped = self._scope(snapshot, changed) if changed else snapshot
        findings: list[PatternFinding] = []
        findings.extend(self._burner_phone(scoped, cfg))
        findings.extend(self._structuring(scoped, cfg))
        return findings

    def detect_scheduled(
        self,
        snapshot: CaseGraphSnapshot,
        *,
        centrality: Any | None = None,
        excluded_doc_ids: Iterable[str] | None = None,
        thresholds: dict[str, float] | None = None,
    ) -> list[PatternFinding]:
        """Whole-graph checks run nightly (Redis-locked to one run per case)."""
        cfg = self._config(thresholds)
        excluded = set(excluded_doc_ids or ())
        scoped = self._exclude_docs(snapshot, excluded)
        findings: list[PatternFinding] = []
        findings.extend(self._structuring(scoped, cfg))
        findings.extend(self._burner_phone(scoped, cfg))
        findings.extend(self._rapid_movement(scoped, cfg))
        if centrality is not None:
            findings.extend(self._network_bridge(scoped, centrality, cfg))
        return findings

    # ----------------------------------------------------------------- rules
    def _structuring(
        self, snapshot: CaseGraphSnapshot, cfg: dict[str, float]
    ) -> list[PatternFinding]:
        """STRUCTURING — smurfing: many sub-threshold transfers, large total.

        Rule: within a rolling window, the same account pair makes at least N
        transfers each below the reporting threshold, totalling at least the
        cumulative minimum.  Both thresholds are deployment-configurable.
        """
        findings: list[PatternFinding] = []
        by_pair: dict[tuple[str, str], list[Any]] = defaultdict(list)
        for edge in snapshot.edges_by_type("TRANSFER_TO"):
            amount = float(edge.properties.get("amount") or 0.0)
            ts = _parse_ts(edge.properties.get("ts"))
            if amount <= 0 or ts is None:
                continue
            by_pair[(edge.source_key, edge.target_key)].append((ts, amount, edge))

        min_transfers = int(cfg["structuring_min_transfers"])
        window_days = int(cfg["structuring_window_days"])
        max_single = float(cfg["structuring_max_single_amount"])
        min_total = float(cfg["structuring_min_total_amount"])

        for (source, target), transfers in by_pair.items():
            transfers.sort(key=lambda item: item[0])
            for index, (start_ts, _amount, _edge) in enumerate(transfers):
                window = [
                    item
                    for item in transfers[index:]
                    if item[0] - start_ts <= timedelta(days=window_days)
                ]
                small = [item for item in window if item[1] < max_single]
                total = sum(item[1] for item in window)
                if len(small) >= min_transfers and total >= min_total:
                    docs = sorted(
                        {
                            doc
                            for _ts, _amt, edge in window
                            for doc in (edge.properties.get("source_doc_ids")
                                        or [edge.properties.get("source_doc_id")])
                            if doc
                        }
                    )
                    count_ratio = min(1.0, len(small) / float(min_transfers))
                    total_ratio = min(1.0, total / float(min_total))
                    confidence = round(0.4 + 0.3 * count_ratio + 0.3 * total_ratio, 3)
                    findings.append(
                        PatternFinding(
                            pattern_type=PatternType.STRUCTURING,
                            confidence=confidence,
                            entity_keys=[source, target],
                            evidence_doc_ids=docs,
                            explanation=(
                                f"{len(window)} transfers between these two accounts within "
                                f"{window_days} days, {len(small)} of them below the "
                                f"₹{max_single:,.0f} reporting threshold, totalling "
                                f"₹{total:,.0f}. The individual amounts look unremarkable; "
                                "taken together the pattern is consistent with structuring "
                                "to avoid reporting."
                            ),
                            details={
                                "transfers_in_window": len(window),
                                "sub_threshold_transfers": len(small),
                                "window_days": window_days,
                                "total_amount": round(total, 2),
                                "max_single_amount": max_single,
                                "first_transfer": start_ts.isoformat(),
                                "last_transfer": window[-1][0].isoformat(),
                            },
                        )
                    )
                    break
        return findings

    def _burner_phone(
        self, snapshot: CaseGraphSnapshot, cfg: dict[str, float]
    ) -> list[PatternFinding]:
        """BURNER_PHONE — short-lived number with unusually wide call fan-out."""
        findings: list[PatternFinding] = []
        max_lifespan = int(cfg["burner_max_lifespan_days"])
        min_fanout = int(cfg["burner_min_fanout"])

        linked_persons: dict[str, list[str]] = defaultdict(list)
        for edge in snapshot.edges_by_type("USES_PHONE"):
            linked_persons[edge.target_key].append(edge.source_key)

        stats: dict[str, dict[str, Any]] = {}
        for edge in snapshot.edges_by_type("CALLED"):
            for phone, other in ((edge.source_key, edge.target_key), (edge.target_key, edge.source_key)):
                entry = stats.setdefault(
                    phone,
                    {"callees": set(), "first": None, "last": None, "docs": set(), "calls": 0},
                )
                entry["callees"].add(other)
                entry["calls"] += int(edge.properties.get("call_count") or 1)
                for doc in edge.properties.get("source_doc_ids") or [
                    edge.properties.get("source_doc_id")
                ]:
                    if doc:
                        entry["docs"].add(doc)
                for candidate in ("first_ts", "last_ts", "ts"):
                    value = _parse_ts(edge.properties.get(candidate))
                    if value is None:
                        continue
                    if candidate == "first_ts" or candidate == "ts":
                        entry["first"] = value if entry["first"] is None else min(entry["first"], value)
                    if candidate == "last_ts" or candidate == "ts":
                        entry["last"] = value if entry["last"] is None else max(entry["last"], value)

        for phone, entry in stats.items():
            node = snapshot.nodes.get(phone)
            if node is None or node.label != "Phone":
                continue
            first, last = entry["first"], entry["last"]
            if first is None or last is None:
                continue
            lifespan_days = max(0.0, (last - first).total_seconds() / 86400.0)
            fanout = len(entry["callees"])
            if lifespan_days > max_lifespan or fanout < min_fanout:
                continue
            linked = linked_persons.get(phone, [])
            fanout_ratio = min(1.0, fanout / float(min_fanout))
            lifespan_ratio = 1.0 - min(1.0, lifespan_days / float(max(max_lifespan, 1)))
            confidence = round(
                0.4 + 0.35 * fanout_ratio + 0.15 * lifespan_ratio + (0.10 if linked else 0.0), 3
            )
            findings.append(
                PatternFinding(
                    pattern_type=PatternType.BURNER_PHONE,
                    confidence=confidence,
                    entity_keys=[phone, *linked[:3]],
                    evidence_doc_ids=sorted(entry["docs"]),
                    explanation=(
                        f"This number was active for only {lifespan_days:.0f} day(s) "
                        f"but contacted {fanout} distinct numbers"
                        + (
                            f", and is linked to {len(linked)} person(s) already in this case"
                            if linked
                            else ""
                        )
                        + ". Short lifespan combined with wide fan-out is the classic "
                        "burner-phone signature."
                    ),
                    details={
                        "lifespan_days": round(lifespan_days, 1),
                        "distinct_counterparties": fanout,
                        "total_calls": entry["calls"],
                        "first_seen": first.isoformat(),
                        "last_seen": last.isoformat(),
                        "linked_persons": linked[:5],
                    },
                )
            )
        return findings

    def _rapid_movement(
        self, snapshot: CaseGraphSnapshot, cfg: dict[str, float]
    ) -> list[PatternFinding]:
        """RAPID_MOVEMENT — transit speed implied by consecutive sightings.

        Events evidenced only by an anonymous tip are excluded: a tip is not
        competent evidence of a person's physical location.
        """
        findings: list[PatternFinding] = []
        min_kmh = float(cfg["rapid_movement_min_kmh"])

        location_of: dict[str, tuple[str, float, float]] = {}
        for edge in snapshot.edges_by_type("LOCATED_AT"):
            location = snapshot.nodes.get(edge.target_key)
            if location is None:
                continue
            lat = location.properties.get("lat")
            lon = location.properties.get("lon")
            if lat is None or lon is None:
                continue
            location_of[edge.source_key] = (
                str(location.properties.get("address") or location.name),
                float(lat),
                float(lon),
            )

        events_by_person: dict[str, list[tuple[str, Any]]] = defaultdict(list)
        for edge in snapshot.edges_by_type("PARTICIPATED_IN"):
            events_by_person[edge.source_key].append((edge.target_key, edge.properties))

        for person, events in events_by_person.items():
            located: list[tuple[datetime, str, float, float, list[str], dict[str, Any]]] = []
            for event_key, props in events:
                event = snapshot.nodes.get(event_key)
                if event is None:
                    continue
                ts = _parse_ts(event.properties.get("timestamp")) or _parse_ts(props.get("ts"))
                if ts is None or event_key not in location_of:
                    continue
                address, lat, lon = location_of[event_key]
                docs = list(event.properties.get("source_doc_ids") or [])
                located.append((ts, address, lat, lon, docs, event.properties))
            located.sort(key=lambda item: item[0])

            for index in range(1, len(located)):
                prev_ts, prev_addr, prev_lat, prev_lon, prev_docs, _ = located[index - 1]
                curr_ts, curr_addr, curr_lat, curr_lon, curr_docs, curr_props = located[index]
                hours = (curr_ts - prev_ts).total_seconds() / 3600.0
                if hours <= 0:
                    continue
                distance = haversine_km(prev_lat, prev_lon, curr_lat, curr_lon)
                speed = distance / hours
                if speed < min_kmh:
                    continue
                docs = sorted({*(prev_docs or []), *(curr_docs or [])})
                confidence = round(min(0.95, 0.45 + 0.5 * min(1.0, speed / (min_kmh * 2))), 3)
                findings.append(
                    PatternFinding(
                        pattern_type=PatternType.RAPID_MOVEMENT,
                        confidence=confidence,
                        entity_keys=[person],
                        evidence_doc_ids=docs,
                        explanation=(
                            f"Observed at {prev_addr} and then at {curr_addr} "
                            f"{hours:.1f} hours later — {distance:.0f} km apart, implying an "
                            f"average speed of {speed:.0f} km/h, above the "
                            f"{min_kmh:.0f} km/h plausibility threshold."
                        ),
                        details={
                            "from_location": prev_addr,
                            "to_location": curr_addr,
                            "distance_km": round(distance, 1),
                            "hours": round(hours, 2),
                            "implied_kmh": round(speed, 1),
                            "threshold_kmh": min_kmh,
                            "from_ts": prev_ts.isoformat(),
                            "to_ts": curr_ts.isoformat(),
                            "event_type": curr_props.get("event_type"),
                        },
                    )
                )
        return findings

    def _network_bridge(
        self,
        snapshot: CaseGraphSnapshot,
        centrality: Any,
        cfg: dict[str, float],
    ) -> list[PatternFinding]:
        """NETWORK_BRIDGE — high-betweenness node sitting between communities.

        Runs only in the scheduled pass because it depends on whole-graph
        metrics (betweenness percentiles and Louvain communities).
        """
        from app.analytics.centrality import percentile_rank, top_neighbours

        findings: list[PatternFinding] = []
        threshold = float(cfg["network_bridge_percentile"])
        betweenness = getattr(centrality, "betweenness", {}) or {}
        communities = getattr(centrality, "communities", {}) or {}

        for key, node in snapshot.nodes.items():
            if node.label != "Person":
                continue
            if percentile_rank(betweenness, key) < threshold:
                continue
            own_community = communities.get(key)
            if own_community is None:
                continue
            neighbours = top_neighbours(snapshot, key, limit=8)
            other_communities = {
                communities.get(n["provenance_key"])
                for n in neighbours
                if communities.get(n["provenance_key"]) is not None
                and communities.get(n["provenance_key"]) != own_community
            }
            if len(other_communities) < 2:
                continue
            docs = sorted(
                {
                    doc
                    for n in neighbours
                    for doc in (n.get("source_doc_ids") or [n.get("source_doc_id")])
                    if doc
                }
            )
            findings.append(
                PatternFinding(
                    pattern_type=PatternType.NETWORK_BRIDGE,
                    confidence=round(min(0.95, 0.5 + 0.45 * (percentile_rank(betweenness, key) / 100.0)), 3),
                    entity_keys=[key],
                    evidence_doc_ids=docs,
                    explanation=(
                        f"This person sits in the top {100 - threshold:.0f}% of the case by "
                        f"betweenness and touches {len(other_communities)} distinct groups — "
                        "the structural position of a broker or facilitator connecting "
                        "otherwise separate clusters."
                    ),
                    details={
                        "betweenness": round(float(betweenness.get(key, 0.0)), 4),
                        "betweenness_percentile": round(percentile_rank(betweenness, key), 1),
                        "own_community": own_community,
                        "bridged_communities": sorted(c for c in other_communities if c is not None),
                        "neighbour_count": len(neighbours),
                    },
                )
            )
        return findings

    # ------------------------------------------------------------- utilities
    def _config(self, overrides: dict[str, float] | None = None) -> dict[str, float]:
        cfg = {
            "structuring_min_transfers": float(self.settings.structuring_min_transfers),
            "structuring_window_days": float(self.settings.structuring_window_days),
            "structuring_max_single_amount": float(self.settings.structuring_max_single_amount),
            "structuring_min_total_amount": float(self.settings.structuring_min_total_amount),
            "burner_max_lifespan_days": float(self.settings.burner_max_lifespan_days),
            "burner_min_fanout": float(self.settings.burner_min_fanout),
            "rapid_movement_min_kmh": float(self.settings.rapid_movement_min_kmh),
            "network_bridge_percentile": float(self.settings.network_bridge_percentile),
        }
        if overrides:
            for key, value in overrides.items():
                if key in cfg:
                    try:
                        cfg[key] = float(value)
                    except (TypeError, ValueError):
                        continue
        return cfg

    @staticmethod
    def _scope(snapshot: CaseGraphSnapshot, doc_ids: set[str]) -> CaseGraphSnapshot:
        """Restrict a snapshot to edges evidenced by the given documents."""
        from app.domain.models import CaseGraphSnapshot as Snapshot

        edges = [
            edge
            for edge in snapshot.edges
            if doc_ids & set(edge.properties.get("source_doc_ids") or [edge.properties.get("source_doc_id")])
        ]
        keys = {e.source_key for e in edges} | {e.target_key for e in edges}
        return Snapshot(
            case_id=snapshot.case_id,
            nodes={k: v for k, v in snapshot.nodes.items() if k in keys},
            edges=edges,
        )

    @staticmethod
    def _exclude_docs(snapshot: CaseGraphSnapshot, doc_ids: set[str]) -> CaseGraphSnapshot:
        """Drop edges whose only evidence is an excluded document (e.g. a tip)."""
        if not doc_ids:
            return snapshot
        from app.domain.models import CaseGraphSnapshot as Snapshot

        edges = []
        for edge in snapshot.edges:
            docs = set(edge.properties.get("source_doc_ids") or [edge.properties.get("source_doc_id")])
            if docs and docs <= doc_ids:
                continue
            edges.append(edge)
        keys = {e.source_key for e in edges} | {e.target_key for e in edges}
        return Snapshot(
            case_id=snapshot.case_id,
            nodes={k: v for k, v in snapshot.nodes.items() if k in keys},
            edges=edges,
        )
