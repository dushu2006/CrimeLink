"""Deterministic, evidence-backed investigation findings.

This module turns REAL analysis results — the persisted case graph, its
centrality scores and the pattern detections — into consolidated findings an
investigator can review.  Every rule here is deterministic and every finding
carries:

* the entity keys it is about,
* the source document ids that evidence it,
* the concrete facts (amounts, timestamps, counts) drawn from graph edges,
* a confidence band derived from a stated rule,
* and neutral language: a connection is a *lead*, never an accusation.

There is no model invocation in this file by design.  Calling a model here
would make findings untestable and could let it invent evidence; the optional
AI explanation step (gateway explanation role) runs separately in the workflow
and is clearly labelled ``method="ai_assisted"``.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from app.domain.models import CaseGraphSnapshot, GraphNode
from app.logging import get_logger

log = get_logger("crimelink.analytics.findings")

# Findings are leads, not charges.  The wording rules live here so a reviewer
# auditing the product sees the whole vocabulary in one place.
NEUTRAL_VERBS = {
    "financial": "a potentially significant financial connection exists",
    "contact": "a frequent-interaction pattern exists",
    "hub": "an analytically significant entity (high network centrality)",
    "bridge": "a pattern requiring review (network bridge)",
}

FINANCIAL_HIGH_TX = 3       # >= 3 transfers between the two accounts -> HIGH
FINANCIAL_MEDIUM_TX = 2     # 2 transfers -> MEDIUM
CONTACT_HIGH_CALLS = 8      # >= 8 aggregated calls between two phones -> HIGH
CONTACT_MEDIUM_CALLS = 4
HUB_TOP_N = 5


@dataclass(slots=True)
class Finding:
    """A candidate finding before persistence."""

    finding_type: str
    title: str
    narrative: str
    reason: str
    confidence: float
    confidence_band: str
    entity_keys: list[str]
    evidence: list[dict[str, Any]] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def dedupe_key(self) -> tuple:
        return (self.finding_type, tuple(sorted(self.entity_keys)))


def _band_from(count: int, high: int, medium: int) -> tuple[float, str]:
    if count >= high:
        return 0.85, "HIGH"
    if count >= medium:
        return 0.65, "MEDIUM"
    return 0.45, "LOW"


def _person_label(snapshot: CaseGraphSnapshot, key: str | None) -> str:
    if not key:
        return "(unknown person)"
    node = snapshot.nodes.get(key)
    return node.name if node else key


def _edge_docs(edge) -> list[str]:
    props = edge.properties or {}
    docs = props.get("source_doc_ids") or []
    if not docs and props.get("source_doc_id"):
        docs = [props["source_doc_id"]]
    return sorted({d for d in docs if d})


def financial_chain_findings(snapshot: CaseGraphSnapshot) -> list[Finding]:
    """Person —OWNS_ACCOUNT→ Acct —TRANSFER_TO→ Acct ←OWNS_ACCOUNT— Person.

    Each individual TRANSFER_TO edge stays discrete in the graph (structuring
    detection needs the individual amounts); this rule aggregates them per
    account pair and walks ownership out to persons on both sides.  A chain
    only becomes a finding when BOTH ends are owned by known persons —
    an ownerless account transfer is reported nowhere because it would imply
    an attribution the source data does not make.
    """
    owners: dict[str, list[str]] = defaultdict(list)
    for edge in snapshot.edges_by_type("OWNS_ACCOUNT"):
        if edge.target_key in snapshot.nodes and edge.source_key in snapshot.nodes:
            owners[edge.target_key].append(edge.source_key)

    by_pair: dict[tuple[str, str], dict[str, Any]] = {}
    for edge in snapshot.edges_by_type("TRANSFER_TO"):
        senders = owners.get(edge.source_key, [])
        receivers = owners.get(edge.target_key, [])
        if not senders or not receivers:
            continue
        props = edge.properties or {}
        amount = props.get("amount")
        pair = (edge.source_key, edge.target_key)
        agg = by_pair.setdefault(
            pair,
            {"transfers": [], "total": 0.0, "count": 0, "docs": set(), "currency_seen": False},
        )
        try:
            agg["total"] += float(amount)
        except (TypeError, ValueError):
            pass
        agg["count"] += 1
        agg["transfers"].append(
            {
                "amount": amount,
                "ts": props.get("ts"),
                "reference": props.get("reference"),
                "channel": props.get("channel"),
                "from_account": edge.source_key,
                "to_account": edge.target_key,
            }
        )
        agg["docs"].update(_edge_docs(edge))

    findings: list[Finding] = []
    for (acct_a, acct_b), agg in by_pair.items():
        confidence, band = _band_from(agg["count"], FINANCIAL_HIGH_TX, FINANCIAL_MEDIUM_TX)
        for sender in sorted(set(owners[acct_a])):
            for receiver in sorted(set(owners[acct_b])):
                if sender == receiver:
                    continue
                sender_name = _person_label(snapshot, sender)
                receiver_name = _person_label(snapshot, receiver)
                transfers = sorted(agg["transfers"], key=lambda t: str(t.get("ts") or ""))
                first_ts = transfers[0].get("ts") if transfers else None
                last_ts = transfers[-1].get("ts") if transfers else None
                findings.append(
                    Finding(
                        finding_type="FINANCIAL_LINK",
                        title=(
                            f"Repeated financial activity between accounts held by "
                            f"{sender_name} and {receiver_name}"
                        ),
                        narrative=(
                            f"A potentially significant financial connection exists between "
                            f"{sender_name} and {receiver_name}: {agg['count']} transfer(s) "
                            f"totalling {round(agg['total'], 2)} moved from the account associated "
                            f"with {sender_name} to the account associated with {receiver_name}."
                            + (f" First transfer {first_ts}, last {last_ts}." if first_ts else "")
                            + " This pattern requires review; it is not by itself evidence of an offence."
                        ),
                        reason=(
                            f"The case graph links {sender_name} to account {acct_a} via an "
                            f"OWNS_ACCOUNT edge evidenced by the source records, and "
                            f"{receiver_name} to account {acct_b} the same way; "
                            f"{agg['count']} TRANSFER_TO edge(s) connect the accounts, each "
                            "carrying its own amount, timestamp and reference from the "
                            "underlying financial records."
                        ),
                        confidence=confidence,
                        confidence_band=band,
                        entity_keys=sorted({sender, receiver, acct_a, acct_b}),
                        evidence=[
                            {
                                "kind": "relationship",
                                "rel_type": "OWNS_ACCOUNT",
                                "source": sender,
                                "target": acct_a,
                                "source_doc_ids": _edge_docs(
                                    next(
                                        e
                                        for e in snapshot.edges_by_type("OWNS_ACCOUNT")
                                        if e.source_key == sender and e.target_key == acct_a
                                    )
                                ),
                            },
                            {
                                "kind": "relationship",
                                "rel_type": "OWNS_ACCOUNT",
                                "source": receiver,
                                "target": acct_b,
                                "source_doc_ids": _edge_docs(
                                    next(
                                        e
                                        for e in snapshot.edges_by_type("OWNS_ACCOUNT")
                                        if e.source_key == receiver and e.target_key == acct_b
                                    )
                                ),
                            },
                            {
                                "kind": "relationship",
                                "rel_type": "TRANSFER_TO",
                                "from_account": acct_a,
                                "to_account": acct_b,
                                "transfer_count": agg["count"],
                                "total_amount": round(agg["total"], 2),
                                "transfers": transfers[:20],
                                "source_doc_ids": sorted(agg["docs"]),
                            },
                        ],
                        details={
                            "transfer_count": agg["count"],
                            "total_amount": round(agg["total"], 2),
                            "first_ts": first_ts,
                            "last_ts": last_ts,
                        },
                    )
                )
    return findings


def frequent_contact_findings(snapshot: CaseGraphSnapshot) -> list[Finding]:
    """Frequent CALLED aggregates lifted to persons via USES_PHONE.

    CALLED edges are phone-to-phone (aggregated with call_count).  The
    persons are resolved strictly through USES_PHONE edges; a phone with no
    known user produces no finding, because guessing an owner would fabricate
    an attribution.
    """
    users: dict[str, list[str]] = defaultdict(list)
    for edge in snapshot.edges_by_type("USES_PHONE"):
        if edge.source_key in snapshot.nodes:
            users[edge.target_key].append(edge.source_key)

    findings: list[Finding] = []
    seen_pairs: set[tuple[str, str]] = set()
    for edge in snapshot.edges_by_type("CALLED"):
        props = edge.properties or {}
        try:
            call_count = int(props.get("call_count") or 0)
        except (TypeError, ValueError):
            call_count = 0
        if call_count < CONTACT_MEDIUM_CALLS:
            continue
        callers = users.get(edge.source_key, [])
        receivers = users.get(edge.target_key, [])
        if not callers or not receivers:
            continue
        for person_a in sorted(set(callers)):
            for person_b in sorted(set(receivers)):
                if person_a == person_b:
                    continue
                pair = tuple(sorted((person_a, person_b)))
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                confidence, band = _band_from(
                    call_count, CONTACT_HIGH_CALLS, CONTACT_MEDIUM_CALLS
                )
                name_a = _person_label(snapshot, person_a)
                name_b = _person_label(snapshot, person_b)
                findings.append(
                    Finding(
                        finding_type="FREQUENT_CONTACT",
                        title=(
                            f"Frequent interaction between {name_a} and {name_b} "
                            f"({call_count} calls)"
                        ),
                        narrative=(
                            f"A frequent-interaction pattern exists between {name_a} and "
                            f"{name_b}: the phones associated with them exchanged "
                            f"{call_count} call(s)"
                            + (
                                f" between {props.get('first_ts')} and {props.get('last_ts')}"
                                if props.get("first_ts")
                                else ""
                            )
                            + ". Frequent contact is a lead for investigation, not evidence "
                            "of an offence."
                        ),
                        reason=(
                            "The CALLED edge between the two phone nodes carries an "
                            f"aggregated call_count of {call_count} from the call detail "
                            "records; each phone is linked to a person by a USES_PHONE "
                            "edge in the source documents."
                        ),
                        confidence=confidence,
                        confidence_band=band,
                        entity_keys=sorted({person_a, person_b, edge.source_key, edge.target_key}),
                        evidence=[
                            {
                                "kind": "relationship",
                                "rel_type": "CALLED",
                                "source": edge.source_key,
                                "target": edge.target_key,
                                "call_count": call_count,
                                "first_ts": props.get("first_ts"),
                                "last_ts": props.get("last_ts"),
                                "source_doc_ids": _edge_docs(edge),
                            },
                        ],
                        details={"call_count": call_count},
                    )
                )
    return findings


def hub_findings(
    snapshot: CaseGraphSnapshot,
    centrality: dict[str, dict[str, float]] | None,
) -> list[Finding]:
    """Top-centrality persons, stated as analytical significance only."""
    if not centrality:
        return []
    persons = [
        (key, scores)
        for key, scores in centrality.items()
        if key in snapshot.nodes and snapshot.nodes[key].label in ("PERSON", "Person")
    ]
    persons.sort(key=lambda kv: (-kv[1].get("betweenness", 0.0), -kv[1].get("pagerank", 0.0)))
    findings: list[Finding] = []
    for rank, (key, scores) in enumerate(persons[:HUB_TOP_N], start=1):
        node = snapshot.nodes[key]
        betweenness = round(float(scores.get("betweenness") or 0.0), 4)
        if betweenness <= 0:
            continue
        degree = int(scores.get("degree") or 0)
        findings.append(
            Finding(
                finding_type="HIGH_CENTRALITY",
                title=(
                    f"{node.name} ranks #{rank} in the case network by centrality"
                ),
                narrative=(
                    f"{node.name} is an analytically significant entity in this case "
                    f"network: betweenness {betweenness}, degree {degree}. High centrality "
                    "means the person sits on many connection paths; it does not imply "
                    "wrongdoing."
                ),
                reason=(
                    "Computed by the deterministic centrality module from the case "
                    "graph as persisted in the graph store."
                ),
                confidence=0.6,
                confidence_band="MEDIUM",
                entity_keys=[key],
                evidence=[
                    {
                        "kind": "analysis",
                        "method": "centrality",
                        "betweenness": betweenness,
                        "degree": degree,
                        "rank_in_case": rank,
                    }
                ],
                details={"betweenness": betweenness, "degree": degree, "rank": rank},
            )
        )
    return findings


def generate_findings(
    snapshot: CaseGraphSnapshot,
    centrality: dict[str, dict[str, float]] | None = None,
) -> list[Finding]:
    """All deterministic findings for a case, de-duplicated."""
    findings: list[Finding] = []
    seen: set[tuple] = set()
    for finding in (
        *financial_chain_findings(snapshot),
        *frequent_contact_findings(snapshot),
        *hub_findings(snapshot, centrality),
    ):
        key = finding.dedupe_key()
        if key in seen:
            continue
        seen.add(key)
        findings.append(finding)
    log.info("analytics.findings_generated", count=len(findings))
    return findings
