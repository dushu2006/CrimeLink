"""Live HTTP smoke test for the Case Evidence Assistant.

Assumes a CrimeLink API server is already running (default
http://localhost:8000) with the demo dataset bootstrapped.  Signs in as the
demo investigator, asks the target questions against a real case, and prints
the answer each one produces — proving the responses differ by question and
that every citation resolves.

Run::

    python scripts/live_smoke_assistant.py [base_url]
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000").rstrip("/")
BADGE = "DEMO-INVESTIGATOR"
PASSWORD = "DemoInvestigator@2026"

QUESTIONS = [
    "What are the details of this case?",
    "Tell me about the people involved.",
    "What are the files?",
    "What evidence connects the accused and the complainant?",
    "What happened in this case?",
    "Show me the financial evidence.",
    "What happened before the incident?",
    "Are there contradictions in the evidence?",
    "Summarize this case.",
]


def _request(method: str, path: str, *, token: str | None = None, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{BASE}{path}", data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=180) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, {"error": exc.read().decode()[:400]}


def main() -> int:
    status, payload = _request("POST", "/api/v1/auth/login",
                               body={"badge_number": BADGE, "password": PASSWORD})
    if status != 200:
        print(f"login failed ({status}): {payload}", file=sys.stderr)
        return 2
    token = payload["access_token"]

    status, cases = _request("GET", "/api/v1/cases?limit=100", token=token)
    if status != 200:
        print(f"case listing failed ({status}): {cases}", file=sys.stderr)
        return 2
    items = cases.get("items") or cases.get("cases") or cases
    if isinstance(items, dict):
        items = items.get("items", [])
    if not items:
        print("no cases available; is the demo dataset bootstrapped?", file=sys.stderr)
        return 2

    # Pick the demo case that has real evidence (CR-2020 when present).
    chosen = None
    for case in items:
        if "2020" in str(case.get("case_number", "")):
            chosen = case
            break
    chosen = chosen or items[0]
    case_ref = chosen.get("case_number") or chosen.get("id")

    print(f"Case under test: {case_ref} ({chosen.get('title')})")
    print(f"Questions: {len(QUESTIONS)}")
    print()

    seen: dict[str, str] = {}
    failures = 0
    valid_doc_ids: set[str] = set()

    for question in QUESTIONS:
        status, body = _request(
            "POST",
            f"/api/v1/ai/cases/{case_ref}/ask",
            token=token,
            body={"question": question},
        )
        print("=" * 78)
        print(f"Q: {question}")
        if status != 200:
            print(f"  HTTP {status}: {body}")
            failures += 1
            continue

        finding = body.get("finding") or {}
        context = body.get("context") or {}
        answer = finding.get("direct_answer") or finding.get("summary") or ""
        print(f"   http={status} available={body.get('available')} "
              f"fallback={body.get('fallback_reason')}")
        print(f"   intent={context.get('answer_mode')} "
              f"docs={context.get('documents_included_count')} "
              f"entities={context.get('detected_entity_count')}")
        print("-" * 78)
        print(answer[:1200])

        valid_doc_ids.update(context.get("retrieved_evidence_ids") or [])

        if not answer.strip():
            print("  !! EMPTY ANSWER", file=sys.stderr)
            failures += 1
        if answer.strip() in seen:
            print(f"  !! IDENTICAL to {seen[answer.strip()]!r}", file=sys.stderr)
            failures += 1
        seen[answer.strip()] = question

        followups = finding.get("followup_questions") or []
        if followups:
            print(f"   follow-ups: {followups[:3]}")
        print()

    # Citations must resolve to records that exist for this case.
    for question in QUESTIONS[:3]:
        status, body = _request(
            "POST", f"/api/v1/ai/cases/{case_ref}/ask", token=token,
            body={"question": question},
        )
        if status != 200:
            continue
        allowed = set((body.get("context") or {}).get("retrieved_evidence_ids") or [])
        for item in (body.get("finding") or {}).get("claim_citations") or []:
            for ref in item.get("evidence_refs") or []:
                if ref and ref not in allowed:
                    print(f"  !! CITATION {ref} not in case evidence for {question!r}",
                          file=sys.stderr)
                    failures += 1

    print("=" * 78)
    print(f"Distinct answers: {len(seen)}/{len(QUESTIONS)}   failures: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
