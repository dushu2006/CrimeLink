"""Deterministic suspicious-pattern detectors.

Eleven detectors, zero models. Each one reads the in-scope snapshot (plus
document metadata, centrality, engine findings, and review state) and emits
:class:`SuspiciousPattern` records with evidence, strength, contradictions
considered, and innocent alternatives.

Decoys are first-class output, not silent drops: investigator-dismissed
combinations, benign-only pairs (family / household / organisation),
low-confidence social-only links, and single co-locations return as
``excluded=True`` entries labelled COINCIDENCE with reasons — visible in
the workspace under "set aside", never hidden.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.analytics.patterns import haversine_km
from app.domain.enums import LOW_CONFIDENCE_REL_TYPES
from app.domain.models import CaseGraphSnapshot

from .evidence import (
    confidence_label,
    doc_pointer,
    edge_pointer,
    make_evidence,
    metric_pointer,
)
from .labels import (
    COINCIDENCE,
    CORROBORATED_LEAD,
    FACT,
    HYPOTHESIS,
    LEAD,
    score_strength,
)
from .schemas import EvidenceItem, ProvenanceItem, SuspiciousPattern

#: Communication anomaly: a pair needs this many calls AND a z-score above
#: the pair distribution before it is suspicious rather than merely chatty.
MIN_CALLS_ANOMALY = 10
ZSCORE_ANOMALY = 2.0

#: Co-location: within this distance, on enough distinct dates or places.
COLOC_KM = 1.0
COLOC_MIN_DATES = 2
COLOC_MIN_PLACES = 2

#: Repeated combination: the same pair named across this many documents.
REPEATED_MIN_DOCS = 3

#: Temporal burst: this many dated edges inside any 48-hour window.
BURST_WINDOW_HOURS = 48
BURST_MIN_EDGES = 3

#: Network bridge: normalized betweenness at least this fraction of the max,
#: with a minimum degree so lone connectors do not qualify.
BRIDGE_SCORE = 0.90
BRIDGE_MIN_DEGREE = 3

#: Community signal: groups smaller than this are pairs, not communities.
MIN_COMMUNITY = 3

#: Financial flow: this many transfers touching one account in a week.
FINANCIAL_MIN_TRANSFERS = 5
FINANCIAL_WINDOW_DAYS = 7

#: Transparency cap: at most this many set-aside entries per answer.
MAX_EXCLUDED = 10

LOCATION_LABELS = frozenset({"LOCATION", "ADDRESS", "PLACE"})
PRESENCE_RELS = frozenset({"PRESENT_AT", "OBSERVED_AT", "LOCATED_AT", "VISITED", "SEEN_AT"})
USE_RELS = frozenset({"USES", "DRIVES", "DROVE", "OPERATES", "TRAVELLED_IN", "RIDES"})
OWN_RELS = frozenset({"OWNS", "OWNED_BY", "REGISTERED_TO", "REGISTERED_UNDER", "TITLE_HOLDER"})
FAMILY_RELS = frozenset(
    {"SPOUSE", "MARRIED_TO", "PARENT_OF", "CHILD_OF", "SIBLING_OF", "FAMILY_MEMBER", "RELATIVE_OF"}
)
HOUSEHOLD_RELS = frozenset({"LIVES_WITH", "HOUSEHOLD_MEMBER", "SHARES_ADDRESS"})
ORG_RELS = frozenset({"WORKS_FOR", "EMPLOYED_BY", "MEMBER_OF", "STUDIES_AT", "ENROLLED_AT"})
BENIGN_RELS = FAMILY_RELS | HOUSEHOLD_RELS | ORG_RELS
META_RELS = frozenset({"MERGED_INTO", "POTENTIAL_ALIAS", "SIMILARITY_REJECTED"})

_TS_KEYS = ("timestamp", "observed_at", "call_date", "call_time", "datetime", "date", "start_time")
_LAT_KEYS = ("lat", "latitude")
_LON_KEYS = ("lon", "lng", "lonitude", "longitude")


@dataclass
class DetectorContext:
    """Everything the detectors may read; nothing they may mutate."""

    snapshot: CaseGraphSnapshot
    doc_index: dict[str, dict] = field(default_factory=dict)
    centrality: object | None = None
    engine_findings: list = field(default_factory=list)
    analytics_findings: list = field(default_factory=list)
    incident_ts: datetime | None = None
    pending_aliases: list = field(default_factory=list)
    dismissed_signatures: set[str] = field(default_factory=set)
    dismissed_notes: dict[str, str] = field(default_factory=dict)


def pair_signature(kind: str, entity_keys: list[str]) -> str:
    """Stable dismissal signature for one detector firing."""
    return f"{kind}:" + "|".join(sorted(entity_keys))


def entity_signature(entity_keys: list[str]) -> str:
    """Kind-agnostic signature: the same combination, any detector."""
    return "|".join(sorted(entity_keys))


def _node_cases(node) -> list[str]:
    cases = (node.properties or {}).get("case_ids") or []
    return [str(case) for case in cases if case]


def _node_name(snapshot: CaseGraphSnapshot, key: str) -> str:
    node = (snapshot.nodes or {}).get(key)
    if node is None:
        return key
    return node.name or key


def _edge_docs(edges: list) -> set[str]:
    docs: set[str] = set()
    for edge in edges:
        doc_id = (edge.properties or {}).get("source_doc_id")
        if doc_id:
            docs.add(str(doc_id))
    return docs


def _parse_ts(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
            try:
                return datetime.strptime(text[: len(fmt) + 2], fmt)
            except ValueError:
                continue
    return None


def _edge_ts(edge) -> datetime | None:
    props = edge.properties or {}
    for key in _TS_KEYS:
        parsed = _parse_ts(props.get(key))
        if parsed is not None:
            return parsed
    return None


def _coords(node) -> tuple[float, float] | None:
    props = node.properties or {}
    lat = next((props.get(key) for key in _LAT_KEYS if props.get(key) is not None), None)
    lon = next((props.get(key) for key in _LON_KEYS if props.get(key) is not None), None)
    try:
        if lat is None or lon is None:
            return None
        return float(lat), float(lon)
    except (TypeError, ValueError):
        return None


def _doc_evidence(
    ctx: DetectorContext,
    doc_id: str,
    summary: str,
    *,
    stance: str = "supports",
) -> EvidenceItem:
    info = ctx.doc_index.get(doc_id, {})
    label = confidence_label(info.get("source_confidence"))
    origin = info.get("origin_file") or info.get("filename")
    pointer = doc_pointer(
        doc_id=doc_id,
        label=str(info.get("filename") or info.get("origin_file") or doc_id),
        origin_file=str(origin) if origin else None,
        content_hash=info.get("content_hash"),
        detail=str(info.get("document_type")) if info.get("document_type") else None,
    )
    return make_evidence("document", summary, label=label, stance=stance, provenance=[pointer])


def _edge_evidence(edge, summary: str, *, stance: str = "supports") -> EvidenceItem:
    key = getattr(edge, "key", "") or f"{edge.source_key}->{edge.target_key}"
    pointer = edge_pointer(edge_key=str(key), label=f"{edge.rel_type} edge")
    return make_evidence("relationship", summary, label=FACT, stance=stance, provenance=[pointer])


def _pair_edges(snapshot: CaseGraphSnapshot, first: str, second: str) -> list:
    return [
        edge
        for edge in (snapshot.edges or [])
        if edge.rel_type not in META_RELS
        and {edge.source_key, edge.target_key} == {first, second}
    ]


def _all_pairs(snapshot: CaseGraphSnapshot) -> dict[tuple[str, str], list]:
    grouped: dict[tuple[str, str], list] = defaultdict(list)
    for edge in snapshot.edges or []:
        if edge.rel_type in META_RELS:
            continue
        grouped[tuple(sorted((edge.source_key, edge.target_key)))].append(edge)
    return grouped


def _benign_kind(edges: list) -> str | None:
    rels = {edge.rel_type for edge in edges}
    if not rels or not rels <= BENIGN_RELS:
        return None
    if rels <= FAMILY_RELS:
        return "family"
    if rels <= HOUSEHOLD_RELS:
        return "household"
    if rels <= ORG_RELS:
        return "organisation"
    return "family/household/organisation"


def _excluded(
    *,
    kind: str,
    title: str,
    explanation: str,
    reason: str,
    entities: list[str],
    entity_keys: list[str],
    evidence: list[EvidenceItem] | None = None,
    provenance: list[ProvenanceItem] | None = None,
) -> SuspiciousPattern:
    strength, factors = score_strength(independent_sources=0)
    return SuspiciousPattern(
        kind=kind,
        title=title,
        explanation=explanation,
        entities=entities,
        entity_keys=entity_keys,
        evidence=list(evidence or []),
        inference_label=COINCIDENCE,
        strength=strength,
        strength_factors=factors,
        contradictions_considered=[reason],
        innocent_alternatives=[reason],
        excluded=True,
        exclusion_reason=reason,
        provenance=list(provenance or []),
    )


def _apply_dismissal(
    ctx: DetectorContext, pattern: SuspiciousPattern
) -> SuspiciousPattern:
    """A reviewer has the last word: dismissed combinations stay visible."""
    sigs = {
        pair_signature(pattern.kind, pattern.entity_keys),
        entity_signature(pattern.entity_keys),
    }
    hit = next((sig for sig in sigs if sig in ctx.dismissed_signatures), None)
    if hit is None:
        return pattern
    note = ctx.dismissed_notes.get(hit, "Dismissed by a reviewer.")
    pattern.excluded = True
    pattern.exclusion_reason = f"Previously dismissed: {note}"
    pattern.inference_label = COINCIDENCE
    pattern.contradictions_considered = [*pattern.contradictions_considered, note]
    return pattern


def detect_cross_case_entities(ctx: DetectorContext) -> list[SuspiciousPattern]:
    """One canonical entity appearing in two or more in-scope cases."""
    found: list[SuspiciousPattern] = []
    for key, node in (ctx.snapshot.nodes or {}).items():
        cases = sorted(set(_node_cases(node)))
        if len(cases) < 2:
            continue
        name = node.name or key
        evidence = [
            make_evidence(
                "record",
                f"{name} is attributed to {len(cases)} in-scope cases.",
                label=FACT,
                provenance=[metric_pointer(name=f"case-span:{key}", label=f"spans {len(cases)} cases")],
            )
        ]
        strength, factors = score_strength(independent_sources=len(cases))
        found.append(
            SuspiciousPattern(
                kind="CROSS_CASE_ENTITY",
                title=f"{name} appears in {len(cases)} cases",
                explanation=(
                    f"The same canonical record ({key}) carries case attribution "
                    f"in {len(cases)} in-scope cases. Cross-case presence is worth "
                    "checking and proves nothing by itself."
                ),
                entities=[name],
                entity_keys=[key],
                cases=cases,
                evidence=evidence,
                inference_label=LEAD if strength in ("WEAK", "INSUFFICIENT") else CORROBORATED_LEAD,
                strength=strength,
                strength_factors=factors,
                contradictions_considered=[
                    "The cases may legitimately share a witness, victim, or official."
                ],
                innocent_alternatives=[
                    "Same person, unrelated roles in each case (e.g. witness in one, complainant in another)."
                ],
            )
        )
    return found


def detect_cross_case_links(ctx: DetectorContext) -> list[SuspiciousPattern]:
    """Edges whose endpoints sit in disjoint case sets (a spanning link)."""
    found: list[SuspiciousPattern] = []
    nodes = ctx.snapshot.nodes or {}
    for edge in ctx.snapshot.edges or []:
        if edge.rel_type in META_RELS:
            continue
        first_cases = set(_node_cases(nodes[edge.source_key])) if edge.source_key in nodes else set()
        second_cases = set(_node_cases(nodes[edge.target_key])) if edge.target_key in nodes else set()
        if not first_cases or not second_cases or not first_cases.isdisjoint(second_cases):
            continue
        first, second = _node_name(ctx.snapshot, edge.source_key), _node_name(
            ctx.snapshot, edge.target_key
        )
        docs = _edge_docs([edge])
        strength, factors = score_strength(independent_sources=max(1, len(docs)))
        evidence = [_edge_evidence(edge, f"{first} —[{edge.rel_type}]→ {second} spanning disjoint cases.")]
        for doc_id in sorted(docs):
            evidence.append(_doc_evidence(ctx, doc_id, f"Recorded in {doc_id}."))
        found.append(
            SuspiciousPattern(
                kind="CROSS_CASE_LINK",
                title=f"{first} ↔ {second}: link spans cases",
                explanation=(
                    f"A {edge.rel_type} link connects records from disjoint case sets "
                    f"({sorted(first_cases)} vs {sorted(second_cases)}). Spanning links "
                    "are how separate cases turn out to share a network."
                ),
                entities=[first, second],
                entity_keys=[edge.source_key, edge.target_key],
                cases=sorted(first_cases | second_cases),
                evidence=evidence,
                inference_label=LEAD,
                strength=strength,
                strength_factors=factors,
                contradictions_considered=["Case attribution may be incomplete on either endpoint."],
                innocent_alternatives=["Legitimate cross-case contact (family, business, official duty)."],
            )
        )
    return found


def detect_communication_anomalies(ctx: DetectorContext) -> list[SuspiciousPattern]:
    """Call pairs that are both voluminous (≥10) and statistical outliers."""
    counts: dict[tuple[str, str], int] = defaultdict(int)
    pair_edges: dict[tuple[str, str], list] = defaultdict(list)
    for edge in ctx.snapshot.edges or []:
        if edge.rel_type != "CALLED":
            continue
        pair = tuple(sorted((edge.source_key, edge.target_key)))
        weight = edge.properties.get("count", 1) if edge.properties else 1
        try:
            weight = max(1, int(weight))
        except (TypeError, ValueError):
            weight = 1
        counts[pair] += weight
        pair_edges[pair].append(edge)
    if not counts:
        return []
    values = list(counts.values())
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    spread = math.sqrt(variance)
    found: list[SuspiciousPattern] = []
    for pair in sorted(counts):
        total = counts[pair]
        if total < MIN_CALLS_ANOMALY:
            continue
        zscore = (total - mean) / spread if spread > 0 else 0.0
        if len(counts) > 1 and zscore < ZSCORE_ANOMALY:
            continue
        first, second = _node_name(ctx.snapshot, pair[0]), _node_name(ctx.snapshot, pair[1])
        docs = _edge_docs(pair_edges[pair])
        strength, factors = score_strength(
            independent_sources=max(1, len(docs)),
            corroborating_records=total,
            notes=[f"{total} calls, z-score {zscore:.2f} across {len(counts)} pairs."],
        )
        evidence = [
            make_evidence(
                "metric",
                f"{total} calls between {first} and {second} (z-score {zscore:.2f}).",
                label=FACT,
                provenance=[
                    metric_pointer(name=f"calls:{pair[0]}:{pair[1]}", label=f"{total} calls")
                ],
            )
        ]
        for doc_id in sorted(docs):
            evidence.append(_doc_evidence(ctx, doc_id, f"Call records in {doc_id}."))
        found.append(
            SuspiciousPattern(
                kind="COMMUNICATION_ANOMALY",
                title=f"{first} ↔ {second}: {total} calls",
                explanation=(
                    f"{total} calls is an outlier (z-score {zscore:.2f}) against the "
                    "in-scope pair distribution. Volume alone never establishes intent."
                ),
                entities=[first, second],
                entity_keys=[pair[0], pair[1]],
                evidence=evidence,
                inference_label=LEAD,
                strength=strength,
                strength_factors=factors,
                contradictions_considered=["High-volume legitimate contact (family, business, wrong numbers)."],
                innocent_alternatives=["Routine frequent contact with an innocent purpose."],
            )
        )
    return found


def detect_financial_flows(ctx: DetectorContext) -> list[SuspiciousPattern]:
    """Accounts with a burst of transfers inside a seven-day window."""
    by_account: dict[str, list] = defaultdict(list)
    for edge in ctx.snapshot.edges or []:
        if edge.rel_type != "TRANSFER_TO":
            continue
        by_account[edge.source_key].append(edge)
        by_account[edge.target_key].append(edge)
    found: list[SuspiciousPattern] = []
    window = timedelta(days=FINANCIAL_WINDOW_DAYS)
    for account in sorted(by_account):
        dated = sorted(
            ((stamp, edge) for edge in by_account[account] if (stamp := _edge_ts(edge))),
            key=lambda item: item[0],
        )
        if len(dated) < FINANCIAL_MIN_TRANSFERS:
            continue
        burst: list[tuple[datetime, object]] = []
        for index, (stamp, _edge) in enumerate(dated):
            inside = [(other, other_edge) for other, other_edge in dated if stamp <= other <= stamp + window]
            if len(inside) >= FINANCIAL_MIN_TRANSFERS and len(inside) > len(burst):
                burst = inside
        if not burst:
            continue
        name = _node_name(ctx.snapshot, account)
        docs = _edge_docs([edge for _stamp, edge in burst])
        strength, factors = score_strength(
            independent_sources=max(1, len(docs)),
            corroborating_records=len(burst),
            temporal_relevance="clustered",
        )
        start, end = burst[0][0].date().isoformat(), burst[-1][0].date().isoformat()
        evidence = [
            make_evidence(
                "metric",
                f"{len(burst)} transfers touch {name} between {start} and {end}.",
                label=FACT,
                provenance=[metric_pointer(name=f"transfers:{account}", label=f"{len(burst)} transfers")],
            )
        ]
        for doc_id in sorted(docs):
            evidence.append(_doc_evidence(ctx, doc_id, f"Transfer records in {doc_id}."))
        found.append(
            SuspiciousPattern(
                kind="FINANCIAL_FLOW",
                title=f"{name}: {len(burst)} transfers in {FINANCIAL_WINDOW_DAYS} days",
                explanation=(
                    f"{len(burst)} transfers touch one account inside a {FINANCIAL_WINDOW_DAYS}-day "
                    f"window ({start} to {end}). Bursts deserve a look; most have ordinary explanations."
                ),
                entities=[name],
                entity_keys=[account],
                time_range={"start": start, "end": end},
                evidence=evidence,
                inference_label=LEAD,
                strength=strength,
                strength_factors=factors,
                contradictions_considered=["Salary runs, loan disbursal, festival-season business."],
                innocent_alternatives=["Legitimate business or household money movement."],
            )
        )
    return found


def detect_vehicle_mismatches(ctx: DetectorContext) -> list[SuspiciousPattern]:
    """Use ≠ ownership: someone drives what someone else holds title to."""
    nodes = ctx.snapshot.nodes or {}
    uses: list[tuple[str, str, object]] = []
    owners: dict[str, set[str]] = defaultdict(set)
    for edge in ctx.snapshot.edges or []:
        if edge.rel_type in USE_RELS:
            uses.append((edge.source_key, edge.target_key, edge))
        elif edge.rel_type in OWN_RELS:
            if edge.rel_type in {"OWNS", "TITLE_HOLDER"}:
                owners[edge.target_key].add(edge.source_key)
            else:
                owners[edge.source_key].add(edge.target_key)
    found: list[SuspiciousPattern] = []
    for user_key, vehicle_key, edge in uses:
        vehicle = nodes.get(vehicle_key)
        if vehicle is None or (vehicle.label or "").upper() not in {"VEHICLE", "CAR", "BIKE", "TRUCK"}:
            if vehicle is not None and "plate" not in (vehicle.properties or {}):
                continue
        holders = {holder for holder in owners.get(vehicle_key, set()) if holder != user_key}
        if not holders:
            continue
        user, vehicle_name = _node_name(ctx.snapshot, user_key), _node_name(ctx.snapshot, vehicle_key)
        holder_names = sorted(_node_name(ctx.snapshot, holder) for holder in holders)
        docs = _edge_docs([edge])
        strength, factors = score_strength(independent_sources=max(1, len(docs)))
        evidence = [
            _edge_evidence(edge, f"{user} uses {vehicle_name} ({edge.rel_type})."),
            make_evidence(
                "record",
                f"{vehicle_name} is held by {', '.join(holder_names)}.",
                label=FACT,
            ),
        ]
        for doc_id in sorted(docs):
            evidence.append(_doc_evidence(ctx, doc_id, f"Usage record in {doc_id}."))
        found.append(
            SuspiciousPattern(
                kind="VEHICLE_USE_OWNERSHIP_MISMATCH",
                title=f"{user} uses {vehicle_name} held by {holder_names[0]}",
                explanation=(
                    f"{user} is recorded using {vehicle_name}, whose registered holder is "
                    f"{', '.join(holder_names)}. Borrowed vehicles are common; borrowed "
                    "vehicles around an offence are worth checking."
                ),
                entities=[user, vehicle_name, *holder_names],
                entity_keys=[user_key, vehicle_key, *sorted(holders)],
                evidence=evidence,
                inference_label=LEAD,
                strength=strength,
                strength_factors=factors,
                contradictions_considered=["Family car, employer vehicle, rental, recent sale not yet recorded."],
                innocent_alternatives=["Borrowed or hired with the holder's knowledge."],
            )
        )
    return found


def detect_colocations(ctx: DetectorContext) -> list[SuspiciousPattern]:
    """Pairs repeatedly in the same place: ≥2 dates near, or ≥2 places.

    A single shared presence is not a pattern — it is emitted as a
    set-aside COINCIDENCE so the investigator sees it was considered.
    """
    nodes = ctx.snapshot.nodes or {}
    presence: dict[str, list[tuple[str, datetime | None]]] = defaultdict(list)
    for edge in ctx.snapshot.edges or []:
        if edge.rel_type not in PRESENCE_RELS:
            continue
        person, place = edge.source_key, edge.target_key
        if place not in nodes or (nodes[place].label or "").upper() not in LOCATION_LABELS:
            person, place = place, person
            if place not in nodes or (nodes[place].label or "").upper() not in LOCATION_LABELS:
                continue
        presence[person].append((place, _edge_ts(edge)))

    people = sorted(presence)
    found: list[SuspiciousPattern] = []
    for index, first in enumerate(people):
        for second in people[index + 1 :]:
            dates: set[str] = set()
            places: set[str] = set()
            for place_a, ts_a in presence[first]:
                for place_b, ts_b in presence[second]:
                    coords_a = _coords(nodes[place_a])
                    coords_b = _coords(nodes[place_b])
                    near = place_a == place_b or (
                        coords_a is not None
                        and coords_b is not None
                        and haversine_km(*coords_a, *coords_b) <= COLOC_KM
                    )
                    if not near:
                        continue
                    places.add(place_a)
                    places.add(place_b)
                    if ts_a is not None and ts_b is not None and ts_a.date() == ts_b.date():
                        dates.add(ts_a.date().isoformat())
            first_name, second_name = _node_name(ctx.snapshot, first), _node_name(ctx.snapshot, second)
            keys = [first, second]
            if len(dates) >= COLOC_MIN_DATES or len(places) >= COLOC_MIN_PLACES:
                strength, factors = score_strength(
                    independent_sources=max(len(dates), 1),
                    temporal_relevance="clustered" if dates else "unknown",
                )
                found.append(
                    SuspiciousPattern(
                        kind="COLOCATION",
                        title=f"{first_name} ↔ {second_name}: together {len(dates)} date(s), {len(places)} place(s)",
                        explanation=(
                            f"The pair is recorded within {COLOC_KM} km on {len(dates)} distinct "
                            f"date(s) across {len(places)} place(s). Repeated proximity is "
                            "suggestive; shared routines explain most of it."
                        ),
                        entities=[first_name, second_name],
                        entity_keys=keys,
                        evidence=[
                            make_evidence(
                                "metric",
                                f"{len(dates)} shared date(s), {len(places)} shared place(s).",
                                label=FACT,
                                provenance=[
                                    metric_pointer(name=f"coloc:{first}:{second}", label="co-location")
                                ],
                            )
                        ],
                        inference_label=LEAD,
                        strength=strength,
                        strength_factors=factors,
                        contradictions_considered=["Shared home, workplace, commute, or neighbourhood."],
                        innocent_alternatives=["Overlapping daily routines with no coordination."],
                    )
                )
            elif places:
                found.append(
                    _excluded(
                        kind="COLOCATION",
                        title=f"{first_name} ↔ {second_name}: single shared presence",
                        explanation="The pair shares exactly one recorded presence — noted, not a pattern.",
                        reason="Single co-location: one shared presence is coincidence until repeated.",
                        entities=[first_name, second_name],
                        entity_keys=keys,
                    )
                )
    return found


def detect_temporal_bursts(ctx: DetectorContext) -> list[SuspiciousPattern]:
    """≥3 dated edges between a pair inside any 48-hour window."""
    found: list[SuspiciousPattern] = []
    window = timedelta(hours=BURST_WINDOW_HOURS)
    for pair, edges in sorted(_all_pairs(ctx.snapshot).items()):
        dated = sorted(
            ((stamp, edge) for edge in edges if (stamp := _edge_ts(edge))),
            key=lambda item: item[0],
        )
        if len(dated) < BURST_MIN_EDGES:
            continue
        burst: list[tuple[datetime, object]] = []
        for stamp, _edge in dated:
            inside = [(other, other_edge) for other, other_edge in dated if stamp <= other <= stamp + window]
            if len(inside) >= BURST_MIN_EDGES and len(inside) > len(burst):
                burst = inside
        if not burst:
            continue
        first, second = _node_name(ctx.snapshot, pair[0]), _node_name(ctx.snapshot, pair[1])
        docs = _edge_docs([edge for _stamp, edge in burst])
        start, end = burst[0][0].isoformat(), burst[-1][0].isoformat()
        strength, factors = score_strength(
            independent_sources=max(1, len(docs)),
            corroborating_records=len(burst),
            temporal_relevance="clustered",
        )
        evidence = [
            make_evidence(
                "metric",
                f"{len(burst)} records between {first} and {second} from {start} to {end}.",
                label=FACT,
                provenance=[metric_pointer(name=f"burst:{pair[0]}:{pair[1]}", label="temporal burst")],
            )
        ]
        for doc_id in sorted(docs):
            evidence.append(_doc_evidence(ctx, doc_id, f"Burst records in {doc_id}."))
        found.append(
            SuspiciousPattern(
                kind="TEMPORAL_BURST",
                title=f"{first} ↔ {second}: {len(burst)} records in {BURST_WINDOW_HOURS}h",
                explanation=(
                    f"{len(burst)} dated records cluster inside a {BURST_WINDOW_HOURS}-hour window. "
                    "Bursts around an incident matter; bursts anywhere else are usually life."
                ),
                entities=[first, second],
                entity_keys=[pair[0], pair[1]],
                time_range={"start": start, "end": end},
                evidence=evidence,
                inference_label=LEAD,
                strength=strength,
                strength_factors=factors,
                contradictions_considered=["Event-driven contact (wedding, emergency, business deadline)."],
                innocent_alternatives=["A busy period with an ordinary trigger."],
            )
        )
    return found


def detect_bridge_signals(ctx: DetectorContext) -> list[SuspiciousPattern]:
    """High-betweenness connectors (≥0.90 of max, degree ≥3)."""
    centrality = ctx.centrality
    betweenness = dict(getattr(centrality, "betweenness", {}) or {})
    degree = dict(getattr(centrality, "degree", {}) or {})
    if not betweenness:
        return []
    peak = max(betweenness.values())
    if peak <= 0:
        return []
    found: list[SuspiciousPattern] = []
    for key in sorted(betweenness):
        score = betweenness[key] / peak
        if score < BRIDGE_SCORE or degree.get(key, 0) < BRIDGE_MIN_DEGREE:
            continue
        name = _node_name(ctx.snapshot, key)
        strength, factors = score_strength(
            independent_sources=1,
            notes=[f"Normalized betweenness {score:.2f}; network position only."],
        )
        found.append(
            SuspiciousPattern(
                kind="NETWORK_BRIDGE",
                title=f"{name}: bridge position (score {score:.2f})",
                explanation=(
                    f"{name} sits on unusually many shortest paths (normalized betweenness "
                    f"{score:.2f}). Graph position is importance, not criminality: hubs are "
                    "often officials, traders, or family elders."
                ),
                entities=[name],
                entity_keys=[key],
                evidence=[
                    make_evidence(
                        "metric",
                        f"Betweenness {score:.2f} of network maximum.",
                        label=FACT,
                        provenance=[
                            metric_pointer(name="centrality:betweenness", label=f"betweenness {score:.2f}")
                        ],
                    )
                ],
                inference_label=HYPOTHESIS,
                strength=strength,
                strength_factors=factors,
                contradictions_considered=["Central hubs are usually legitimate high-contact roles."],
                innocent_alternatives=["A socially or professionally central person."],
            )
        )
    return found


def detect_community_signals(ctx: DetectorContext) -> list[SuspiciousPattern]:
    """Dense groups (≥3) plus high-centrality engine/analytics flags."""
    found: list[SuspiciousPattern] = []
    seen_groups: set[frozenset] = set()
    members = dict(getattr(ctx.centrality, "community_members", {}) or {})
    for _community_id, group in members.items():
        keys = sorted({str(key) for key in group if key in (ctx.snapshot.nodes or {})})
        if len(keys) < MIN_COMMUNITY or frozenset(keys) in seen_groups:
            continue
        seen_groups.add(frozenset(keys))
        names = [_node_name(ctx.snapshot, key) for key in keys]
        strength, factors = score_strength(
            independent_sources=1, notes=["Community membership is a grouping, not an accusation."]
        )
        found.append(
            SuspiciousPattern(
                kind="COMMUNITY_SIGNAL",
                title=f"Group of {len(keys)}: {', '.join(names[:4])}{'…' if len(names) > 4 else ''}",
                explanation=(
                    f"{len(keys)} records cluster as one community. Groups share "
                    "routines — colleagues, neighbours, families — far more often "
                    "than they share intent."
                ),
                entities=names,
                entity_keys=keys,
                evidence=[
                    make_evidence(
                        "metric",
                        f"Community of {len(keys)} members.",
                        label=FACT,
                        provenance=[metric_pointer(name="community:members", label=f"{len(keys)} members")],
                    )
                ],
                inference_label=HYPOTHESIS,
                strength=strength,
                strength_factors=factors,
                contradictions_considered=["Workplace, neighbourhood, or extended family."],
                innocent_alternatives=["An ordinary social or professional circle."],
            )
        )
    for finding in list(ctx.analytics_findings or []) + list(ctx.engine_findings or []):
        raw_code = (
            getattr(finding, "code", "")
            or getattr(finding, "kind", "")
            or getattr(finding, "finding_type", "")
        )
        pattern_type = getattr(finding, "pattern_type", None)
        if pattern_type is not None:
            raw_code = raw_code or getattr(pattern_type, "value", str(pattern_type))
        code = str(raw_code or "").upper()
        if code not in {"HIGH_CENTRALITY", "DENSE_COMMUNITY"}:
            continue
        keys = [str(key) for key in (getattr(finding, "entity_keys", []) or [])][:8]
        if not keys or frozenset(keys) in seen_groups:
            continue
        seen_groups.add(frozenset(keys))
        names = [_node_name(ctx.snapshot, key) for key in keys]
        summary = str(
            getattr(finding, "summary", "")
            or getattr(finding, "explanation", "")
            or getattr(finding, "narrative", "")
            or getattr(finding, "title", "")
            or code
        )
        strength, factors = score_strength(independent_sources=1)
        found.append(
            SuspiciousPattern(
                kind="COMMUNITY_SIGNAL",
                title=f"Flagged group: {', '.join(names[:4])}",
                explanation=f"Analytics flag ({code}): {summary[:300]}",
                entities=names,
                entity_keys=keys,
                evidence=[
                    make_evidence("metric", summary[:300], label=FACT,
                                 provenance=[metric_pointer(name=f"analytics:{code}", label=code)])
                ],
                inference_label=HYPOTHESIS,
                strength=strength,
                strength_factors=factors,
                contradictions_considered=["Analytic flags describe shape, not intent."],
                innocent_alternatives=["A benign dense group."],
            )
        )
    return found


def detect_repeated_combinations(ctx: DetectorContext) -> list[SuspiciousPattern]:
    """Pairs named together across ≥3 distinct documents."""
    found: list[SuspiciousPattern] = []
    for pair, edges in sorted(_all_pairs(ctx.snapshot).items()):
        docs = _edge_docs(edges)
        if len(docs) < REPEATED_MIN_DOCS:
            continue
        first, second = _node_name(ctx.snapshot, pair[0]), _node_name(ctx.snapshot, pair[1])
        benign = _benign_kind(edges)
        if benign:
            found.append(
                _excluded(
                    kind="REPEATED_COMBINATION",
                    title=f"{first} ↔ {second}: repeated but {benign}-only",
                    explanation=f"The pair recurs across {len(docs)} documents, but every link is {benign}-only.",
                    reason=f"Benign-only pair ({benign}); repetition reflects the relationship, not suspicion.",
                    entities=[first, second],
                    entity_keys=[pair[0], pair[1]],
                )
            )
            continue
        strength, factors = score_strength(
            independent_sources=len(docs), corroborating_records=len(edges)
        )
        label = CORROBORATED_LEAD if strength == "STRONG" else LEAD
        evidence = [
            make_evidence(
                "metric",
                f"{first} and {second} co-occur in {len(docs)} documents.",
                label=FACT,
                provenance=[metric_pointer(name=f"cooccur:{pair[0]}:{pair[1]}", label=f"{len(docs)} docs")],
            )
        ]
        for doc_id in sorted(docs):
            evidence.append(_doc_evidence(ctx, doc_id, f"Co-occurrence in {doc_id}."))
        found.append(
            SuspiciousPattern(
                kind="REPEATED_COMBINATION",
                title=f"{first} ↔ {second}: together in {len(docs)} documents",
                explanation=(
                    f"The pair is named together across {len(docs)} distinct documents. "
                    "Repetition across sources is the beginning of corroboration."
                ),
                entities=[first, second],
                entity_keys=[pair[0], pair[1]],
                evidence=evidence,
                inference_label=label,
                strength=strength,
                strength_factors=factors,
                contradictions_considered=["The documents may share one underlying source."],
                innocent_alternatives=["People whose lives genuinely overlap."],
            )
        )
    return found


def detect_er_signals(ctx: DetectorContext) -> list[SuspiciousPattern]:
    """Open identity proposals: candidates the reviewer has not settled."""
    found: list[SuspiciousPattern] = []
    for proposal in ctx.pending_aliases or []:
        source = getattr(proposal, "source_key", "")
        target = getattr(proposal, "target_key", "")
        note = getattr(proposal, "note", "") or "awaiting review"
        first, second = _node_name(ctx.snapshot, source), _node_name(ctx.snapshot, target)
        strength, factors = score_strength(
            independent_sources=0,
            entity_certainty="proposed",
            notes=["A proposal contributes no evidentiary weight until confirmed."],
        )
        found.append(
            SuspiciousPattern(
                kind="ER_SIGNAL",
                title=f"{first} may be {second}",
                explanation=f"An open identity proposal ({note}) links these records. Unconfirmed either way.",
                entities=[first, second],
                entity_keys=[source, target],
                evidence=[
                    make_evidence(
                        "record",
                        f"Open proposal: {first} ↔ {second} ({note}).",
                        label=HYPOTHESIS,
                    )
                ],
                inference_label=HYPOTHESIS,
                strength=strength,
                strength_factors=factors,
                contradictions_considered=["Reviewer may reject the proposal."],
                innocent_alternatives=["Two distinct records that look alike."],
            )
        )
    return found


def detect_social_only_exclusions(ctx: DetectorContext) -> list[SuspiciousPattern]:
    """Pairs linked ONLY by low-confidence social edges: set aside openly."""
    found: list[SuspiciousPattern] = []
    for pair, edges in sorted(_all_pairs(ctx.snapshot).items()):
        rels = {edge.rel_type for edge in edges}
        if not rels or not rels <= set(LOW_CONFIDENCE_REL_TYPES):
            continue
        first, second = _node_name(ctx.snapshot, pair[0]), _node_name(ctx.snapshot, pair[1])
        found.append(
            _excluded(
                kind="SOCIAL_ONLY",
                title=f"{first} ↔ {second}: social link only",
                explanation="The only link is a low-confidence social-media edge.",
                reason="Low-confidence social-only link: online adjacency is not association.",
                entities=[first, second],
                entity_keys=[pair[0], pair[1]],
            )
        )
    return found


_DETECTORS = (
    detect_cross_case_entities,
    detect_cross_case_links,
    detect_communication_anomalies,
    detect_financial_flows,
    detect_vehicle_mismatches,
    detect_colocations,
    detect_temporal_bursts,
    detect_bridge_signals,
    detect_community_signals,
    detect_repeated_combinations,
    detect_er_signals,
)


def detect_all_patterns(
    ctx: DetectorContext,
    *,
    max_patterns: int = 25,
    include_excluded: bool = True,
) -> list[SuspiciousPattern]:
    """Run every detector, apply dismissals, and cap the output."""
    live: list[SuspiciousPattern] = []
    aside: list[SuspiciousPattern] = []
    for detector in _DETECTORS:
        for pattern in detector(ctx):
            if pattern.excluded:
                aside.append(pattern)
            else:
                live.append(_apply_dismissal(ctx, pattern))
    aside.extend(detect_social_only_exclusions(ctx))

    strength_rank = {"STRONG": 0, "MODERATE": 1, "WEAK": 2, "INSUFFICIENT": 3}
    live.sort(key=lambda item: (strength_rank.get(item.strength, 4), item.kind, item.title))

    dismissed_live = [item for item in live if item.excluded]
    live = [item for item in live if not item.excluded]
    aside.extend(dismissed_live)
    aside.sort(key=lambda item: (item.kind, item.title))

    result = live[: max(1, max_patterns)]
    if include_excluded:
        result.extend(aside[:MAX_EXCLUDED])
    return result
