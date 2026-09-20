"""Run the investigative-quality evaluation.

    cd backend
    .venv/bin/python -m evals.investigative_quality.runner --cases CR-2001 CR-2019 CR-2020

By default the suite runs **in-process** against the embedded profile, which is
the deterministic path (no provider key configured).  Passing ``--base-url``
runs the same questions over HTTP against a live API server instead, so the
provider path — when a key *is* configured — can be measured separately and
labelled as such in the output.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[2]
if str(BACKEND_ROOT) not in sys.path:  # pragma: no cover - import bootstrap
    sys.path.insert(0, str(BACKEND_ROOT))

RESULTS_DIR = Path(__file__).resolve().parent / "results"


# --------------------------------------------------------------------------- #
# In-process path
# --------------------------------------------------------------------------- #


def _snapshot_case(case_id: str, snapshot_path: Any) -> tuple[list[dict], list[dict]]:
    """Read the persisted graph snapshot directly (read-only, needs no lock).

    When a live server owns the embedded graph, a second in-process store
    client cannot read it; the snapshot file the server writes is still usable
    ground truth for grading.
    """
    path = Path(str(snapshot_path))
    if not path.exists():
        return [], []
    try:
        payload = json.loads(path.read_text())
    except Exception:
        return [], []
    nodes: list[dict] = []
    for node in payload.get("nodes") or ():
        data = node.get("data") or {}
        keys = set(data.get("case_ids") or ()) | {data.get("case_id")}
        if case_id not in keys:
            continue
        label = str(data.get("entity_type") or data.get("_label") or "")
        if label.upper() == "CASE":
            continue
        nodes.append({
            "provenance_key": node.get("pk"),
            "label": label,
            "properties": dict(data),
            "confidence": data.get("confidence", 1.0),
        })
    known = {str(n["provenance_key"]) for n in nodes}
    edges: list[dict] = []
    for edge in payload.get("edges") or ():
        data = edge.get("data") or {}
        keys = set(data.get("case_ids") or ()) | {data.get("case_id")}
        if case_id not in keys and not (
            str(edge.get("source")) in known and str(edge.get("target")) in known
        ):
            continue
        doc_ids = list(data.get("source_doc_ids") or ())
        if not doc_ids and data.get("source_doc_id"):
            doc_ids = [data["source_doc_id"]]
        edges.append({
            "source_key": edge.get("source"),
            "target_key": edge.get("target"),
            "rel_type": data.get("_rel") or data.get("rel_type") or "",
            "confidence": data.get("confidence", 1.0),
            "timestamp": data.get("timestamp"),
            "source_doc_ids": doc_ids,
            **data,
        })
    return nodes, edges


async def _load_case_data(gateway: Any, case_number: str):
    from sqlalchemy import select

    from app.db.models import Case
    from app.db.session import async_session, init_db

    await init_db()
    async with async_session() as session:
        case = (
            await session.execute(
                select(Case).where(Case.case_number == case_number).limit(1)
            )
        ).scalar_one_or_none()
    if case is None:
        return None
    documents = await gateway._retrieve_case_documents(
        case.id, max_chars_per_doc=gateway.settings.ai_semantic_index_doc_chars
    )
    try:
        nodes = await gateway._get_all_case_nodes(case.id)
    except Exception:
        nodes = []
    try:
        edges = await gateway._get_all_case_edges(case.id)
    except Exception:
        edges = []
    graph_live = bool(nodes and edges)
    if not graph_live:
        snap_nodes, snap_edges = _snapshot_case(case.id, gateway.settings.graph_snapshot_path)
        nodes = nodes or snap_nodes
        edges = edges or snap_edges
    return case, documents, nodes, edges, graph_live


async def run_in_process(
    case_numbers: list[str], *, limit: int | None, categories: set[str] | None
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    from app.ai.gateway import AIGateway
    from evals.investigative_quality.case_facts import build_case_facts, shared_names
    from evals.investigative_quality.graders import grade, observe
    from evals.investigative_quality.question_bank import CATEGORY_TITLES, build_questions

    gateway = AIGateway()
    cases: dict[str, Any] = {}
    raw: dict[str, Any] = {}
    graph_states: list[str] = []
    for case_number in case_numbers:
        loaded = await _load_case_data(gateway, case_number)
        if loaded is None:
            print(f"  ! {case_number} not found in the demo database", file=sys.stderr)
            continue
        case, documents, nodes, edges, graph_live = loaded
        if not graph_live:
            graph_states.append(case_number)
        cases[case_number] = build_case_facts(
            case_id=case.id,
            case_number=case_number,
            title=case.title or "",
            status=str(getattr(case.status, "value", case.status)),
            documents=documents,
            nodes=nodes,
            edges=edges,
        )
        raw[case_number] = case

    results: list[dict[str, Any]] = []
    mode = "DETERMINISTIC"
    ordered = [c for c in case_numbers if c in cases]
    for index, case_number in enumerate(ordered):
        facts = cases[case_number]
        others = [cases[o] for o in ordered if o != case_number]
        foreign = others[index % len(others)] if others else facts
        shared = sorted(
            {name for names in shared_names(facts, others).values() for name in names}
        )
        questions = build_questions(facts, foreign=foreign, shared=shared)
        if categories:
            questions = [q for q in questions if q.category in categories]
        if limit:
            questions = questions[:limit]
        for question in questions:
            response = await gateway.ask(
                question=question.question,
                case_id=facts.case_id,
                principal_id="evaluation",
                dataset_id=None,
                graph_ready=True,
            )
            finding = response.finding
            answer = (finding.direct_answer or finding.summary or "").strip()
            claims = [
                {"claim": claim.claim, "evidence_refs": list(claim.evidence_refs or [])}
                for claim in (finding.claims or ())
            ]
            context = response.context or {}
            observed = observe(
                answer=answer,
                claims=claims,
                retrieved=context.get("retrieved_evidence_ids") or [],
                context=context,
                pseudonymized=bool(getattr(response, "pseudonymized", False)),
                available=bool(response.available),
                fallback_reason=response.fallback_reason,
            )
            record = grade(question, facts, foreign, observed)
            record["category_title"] = CATEGORY_TITLES.get(question.category)
            record["expectation"] = {
                "expect_types": [list(g) for g in question.expect_types],
                "relevant_types": list(question.relevant_types),
                "needs_citation": question.needs_citation,
                "requires_boundary": question.requires_boundary,
                "negative": question.negative,
                "temporal_relation": question.temporal_relation,
                "notes": question.notes,
            }
            record["model"] = {
                "available": response.available,
                "role": response.role,
                "model": response.model,
                "latency_ms": response.latency_ms,
            }
            model_name = str(response.model or "")
            if response.available and model_name and not model_name.startswith("local"):
                mode = "MODEL-ASSISTED"
            results.append(record)
            verdict = "PASS" if record.get("passed") else "FAIL"
            print(f"  [{verdict}] {record['qid']:<12} {question.question[:78]}")
    summary: dict[str, Any] = {"mode": mode, "graph_store_available": not graph_states}
    if graph_states:
        print(
            "WARNING: the embedded graph store was not readable for "
            f"{', '.join(graph_states)} (another process may hold it); "
            "grading used the persisted graph snapshot — stop the API server "
            "for an undegraded deterministic run.",
            file=sys.stderr,
        )
    return results, summary


# --------------------------------------------------------------------------- #
# HTTP path (the same questions through a live server)
# --------------------------------------------------------------------------- #


async def run_over_http(
    case_numbers: list[str], base_url: str, *, limit: int | None, categories: set[str] | None
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Same questions, same ground truth, but answered by a running server.

    Ground truth still comes from the corpus on disk (the server serves that same
    corpus), while every answer is produced by the live HTTP endpoint — so this
    measures the deployed path rather than the library.
    """
    import urllib.error
    import urllib.request

    from app.ai.gateway import AIGateway
    from evals.investigative_quality.case_facts import build_case_facts, shared_names
    from evals.investigative_quality.graders import grade, observe
    from evals.investigative_quality.question_bank import CATEGORY_TITLES, build_questions

    def request(method: str, path: str, *, token: str | None = None, body: dict | None = None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"{base_url}{path}", data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=300) as response:
                return response.status, json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            return exc.code, {"error": exc.read().decode()[:400]}

    status, payload = request(
        "POST", "/api/v1/auth/login",
        body={"badge_number": "DEMO-INVESTIGATOR", "password": "DemoInvestigator@2026"},
    )
    if status != 200:
        raise SystemExit(f"login failed ({status}): {payload}")
    token = payload["access_token"]

    gateway = AIGateway()
    cases: dict[str, Any] = {}
    for case_number in case_numbers:
        loaded = await _load_case_data(gateway, case_number)
        if loaded is None:
            print(f"  ! {case_number} not found in the demo database", file=sys.stderr)
            continue
        case, documents, nodes, edges, _graph_live = loaded
        cases[case_number] = build_case_facts(
            case_id=case.id, case_number=case_number, title=case.title or "",
            status=str(getattr(case.status, "value", case.status)),
            documents=documents, nodes=nodes, edges=edges,
        )

    results: list[dict[str, Any]] = []
    mode = "DETERMINISTIC"
    ordered = [c for c in case_numbers if c in cases]
    for index, case_number in enumerate(ordered):
        facts = cases[case_number]
        others = [cases[o] for o in ordered if o != case_number]
        foreign = others[index % len(others)] if others else facts
        shared = sorted({name for names in shared_names(facts, others).values() for name in names})
        questions = build_questions(facts, foreign=foreign, shared=shared)
        if categories:
            questions = [q for q in questions if q.category in categories]
        if limit:
            questions = questions[:limit]
        for question in questions:
            status, body = request(
                "POST", f"/api/v1/ai/cases/{facts.case_id}/ask",
                token=token, body={"question": question.question},
            )
            if status != 200:
                results.append({
                    "question": question.question, "qid": question.qid,
                    "category": question.category, "case_number": case_number,
                    "checks": {}, "detail": {"http_error": str(body)[:200]},
                    "failure_modes": ["RESPONSE_COMPOSITION_FAILURE"], "passed": False,
                })
                print(f"  [HTTP {status}] {question.qid}")
                continue
            finding = body.get("finding") or {}
            answer = (finding.get("direct_answer") or finding.get("summary") or "").strip()
            claims = [
                {"claim": c.get("claim"), "evidence_refs": list(c.get("evidence_refs") or [])}
                for c in (finding.get("claims") or [])
            ]
            context = body.get("context") or {}
            observed = observe(
                answer=answer, claims=claims,
                retrieved=context.get("retrieved_evidence_ids") or [],
                context=context, pseudonymized=bool(body.get("pseudonymized")),
                available=bool(body.get("available")), fallback_reason=body.get("fallback_reason"),
            )
            record = grade(question, facts, foreign, observed)
            record["category_title"] = CATEGORY_TITLES.get(question.category)
            record["expectation"] = {
                "expect_types": [list(g) for g in question.expect_types],
                "needs_citation": question.needs_citation,
                "requires_boundary": question.requires_boundary,
                "negative": question.negative,
                "notes": question.notes,
            }
            record["model"] = {
                "available": body.get("available"), "role": body.get("role"),
                "model": body.get("model"), "provider": body.get("provider"),
                "latency_ms": body.get("latency_ms"),
                "deterministic_fallback": context.get("deterministic_fallback"),
            }
            # MODEL-ASSISTED only when a model actually answered.
            if body.get("model") and not context.get("deterministic_fallback"):
                mode = "MODEL-ASSISTED"
            results.append(record)
            verdict = "PASS" if record.get("passed") else "FAIL"
            print(f"  [{verdict}] {record['qid']:<12} {question.question[:78]}")
    return results, {"mode": mode, "base_url": base_url}


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #


def scorecard_markdown(summary: dict[str, Any], results: list[dict[str, Any]], cases: list[str]) -> str:
    from evals.investigative_quality.question_bank import CATEGORY_TITLES

    lines = [
        "# CrimeLink investigative-quality scorecard",
        "",
        f"Run: {datetime.now(timezone.utc).isoformat(timespec='seconds')} · "
        f"cases: {', '.join(cases)} · mode: **{summary['mode']}**",
        "",
        "## Overall",
        "",
        f"- questions: **{summary['questions']}**",
        f"- passed: **{summary['passed']}**",
        f"- failed: **{summary['failed']}**",
        f"- pass rate: **{(summary['pass_rate'] or 0):.1%}**",
        "",
        "## By category",
        "",
        "| Cat | Category | Questions | Passed | Failed | Pass rate |",
        "|---|---|---|---|---|---|",
    ]
    for category, bucket in sorted(summary["categories"].items()):
        rate = bucket["passed"] / bucket["questions"] if bucket["questions"] else 0
        lines.append(
            f"| {category} | {CATEGORY_TITLES.get(category, '')} | {bucket['questions']} | "
            f"{bucket['passed']} | {bucket['failed']} | {rate:.1%} |"
        )
    lines += ["", "## By check (independent measurements)", "",
              "| Check | Applicable | Passed | Failed | N/A | Pass rate |", "|---|---|---|---|---|---|"]
    for name, bucket in summary["checks"].items():
        rate = "—" if bucket["pass_rate"] is None else f"{bucket['pass_rate']:.1%}"
        lines.append(
            f"| {name} | {bucket['applicable']} | {bucket['passed']} | {bucket['failed']} | "
            f"{bucket['not_applicable']} | {rate} |"
        )
    lines += ["", "## Failure modes", "", "| Mode | Questions affected |", "|---|---|"]
    for mode, count in summary["failure_modes"].items():
        lines.append(f"| {mode} | {count} |")
    lines += ["", "## Failed questions", "",
              "| qid | category | question | failure modes | notes |", "|---|---|---|---|---|"]
    for result in results:
        if result.get("passed"):
            continue
        detail = result.get("detail") or {}
        first_failure = next(
            (detail[name] for name, verdict in (result.get("checks") or {}).items() if verdict is False),
            "",
        )
        lines.append(
            f"| {result.get('qid')} | {result.get('category')} | {result.get('question','')[:90]} | "
            f"{', '.join(result.get('failure_modes') or [])} | {first_failure[:120]} |"
        )
    lines += ["", "## Check detail per question", ""]
    for result in results:
        lines.append(f"### {result.get('qid')} — {result.get('question')}")
        lines.append("")
        lines.append(f"- verdict: **{'PASS' if result.get('passed') else 'FAIL'}**")
        for name, verdict in (result.get("checks") or {}).items():
            mark = {True: "pass", False: "**FAIL**", None: "n/a"}[verdict]
            lines.append(f"- {name}: {mark} — {(result.get('detail') or {}).get(name, '')}")
        if result.get("unsupported_claims"):
            lines.append(f"- unsupported claims: {result['unsupported_claims'][:3]}")
        lines.append("")
    return "\n".join(lines)


async def main_async(args: argparse.Namespace) -> int:
    categories = set(args.categories.split(",")) if args.categories else None
    if args.base_url:
        results, summary = await run_over_http(
            args.cases, args.base_url, limit=args.limit, categories=categories
        )
    else:
        results, summary = await run_in_process(
            args.cases, limit=args.limit, categories=categories
        )

    from evals.investigative_quality.graders import summarise

    aggregate = summarise(results)
    summary.update(aggregate)
    summary["cases"] = args.cases
    summary["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

    out_dir = Path(args.out) if args.out else RESULTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    payload = {"summary": summary, "results": results}
    (out_dir / f"eval_{stamp}.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    (out_dir / "latest.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    (out_dir / "scorecard.md").write_text(scorecard_markdown(summary, results, args.cases))

    print()
    print(f"mode: {summary['mode']}")
    print(f"questions: {summary['questions']}  passed: {summary['passed']}  failed: {summary['failed']}  "
          f"pass rate: {(summary['pass_rate'] or 0):.1%}")
    for category, bucket in sorted(summary["categories"].items()):
        rate = bucket["passed"] / bucket["questions"] if bucket["questions"] else 0
        print(f"  {category}: {bucket['passed']}/{bucket['questions']} ({rate:.0%})")
    print("failure modes:", summary["failure_modes"])
    print(f"artifacts: {out_dir}/latest.json , {out_dir}/scorecard.md")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="CrimeLink investigative-quality evaluation")
    parser.add_argument("--cases", nargs="+", default=["CR-2001", "CR-2019", "CR-2020"])
    parser.add_argument("--base-url", default=None, help="run over HTTP against a live server")
    parser.add_argument("--limit", type=int, default=None, help="only the first N questions per case")
    parser.add_argument("--categories", default=None, help="comma-separated category letters, e.g. C,L")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()
    if not os.environ.get("CRIMELINK_PROFILE"):
        os.environ["CRIMELINK_PROFILE"] = "embedded"
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
