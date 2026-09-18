/**
 * People Page — Center of CrimeLink
 * Person-centric, calm, evidence-first
 * No fake data, no random counts
 * Judge test: Case → People → Relationship → Evidence → Why → Limitations
 * RBAC: Investigator sees Investigate, Viewer read-only
 */

import { useCallback, useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { PersonList } from "../components/investigator/PersonList";
import { masterPersons } from "../api/client";
import { useAuth } from "../store/auth";
import { isInvestigator, getRoleBadge } from "../lib/rbac";
import { useLiveRefresh } from "../lib/useLiveRefresh";
import StaleDataNotice from "../components/common/StaleDataNotice";
import { WhatNext } from "../components/investigator/WhatNext";

export default function PeoplePage() {
  const [people, setPeople] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const session = useAuth((s) => s.session);
  const role = session?.role;
  const investigator = isInvestigator(role as any);
  const roleBadge = getRoleBadge(role as any);

  const caseParam = searchParams.get("case") || undefined;
  const focusParam = searchParams.get("focus") || undefined;

  const load = useCallback(async () => {
    setLoading(true);
    try {
      // People come from the active dataset only.  This used to also pull the
      // whole 575-node entity graph just to count edges per person; the
      // person endpoint already carries the connection count.
      const personsRes = await masterPersons();
      const items = (personsRes as any).items || [];
      setPeople(
        items.map((p: any) => {
          const key = p.provenance_key || p.id;
          return {
            id: key,
            pseudonym: key,
            displayName: p.name || key.slice(0, 12),
            relationshipsCount: p.connections || 0,
            evidenceCount: p.source_doc_ids?.length || 0,
            casesCount: p.case_ids?.length || 0,
            role: p.role ?? null,
            // Authoritative criminal_status only — the star is never inferred.
            criminalStatus: p.criminal_status ?? null,
            isCriminal: Boolean(p.criminal_status),
          };
        }),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // People are re-derived whenever the dataset gains records; a failed refresh
  // is surfaced instead of leaving a stale directory on screen.
  const live = useLiveRefresh(load);

  if (loading) {
    return (
      <div className="people-page">
        <div className="page-header">
          <h1>People</h1>
          <p className="page-subtitle">Person-centric investigation — Who is involved?</p>
          <div className="cl-hierarchy">Case → People → Relationships → Evidence → Explanation → Action</div>
        </div>
        <div className="skeleton-grid">
          {[1,2,3,4,5,6].map((i) => (
            <div key={i} className="skeleton-card"><div className="skeleton-line w-60" /><div className="skeleton-line w-40" /></div>
          ))}
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="people-page">
        <div className="page-header">
          <h1>People</h1>
          <p className="page-subtitle">Person-centric investigation — Who is involved?</p>
        </div>
        <div className="cl-empty">
          <div className="cl-empty-title">People could not be loaded</div>
          <div className="cl-empty-desc">{error}</div>
        </div>
      </div>
    );
  }

  if (people.length === 0) {
    return (
      <div className="people-page">
        <div className="page-header">
          <h1>People</h1>
          <p className="page-subtitle">Person-centric investigation — Who is involved?</p>
          <div className="cl-hierarchy">Case → People → Relationships → Evidence → Explanation → Action</div>
          {caseParam && <div style={{ marginTop: "8px", fontSize: "11px", fontFamily: "var(--font-mono)", color: "var(--muted)" }}>Case: {caseParam} · <span className={`badge badge-${roleBadge.tone}`}>{roleBadge.label}</span></div>}
        </div>
        <div className="cl-empty">
          <div className="cl-empty-title">No people identified yet</div>
          <div className="cl-empty-desc">Search for people to begin investigation. People are the center of CrimeLink. Import or create a dataset to begin.</div>
          <button className="cl-btn cl-btn-primary" onClick={() => navigate("/cases")} style={{ marginTop: "12px" }}>View Cases</button>
        </div>
      </div>
    );
  }

  return (
    <div className="people-page">
      <StaleDataNotice
        error={live.error}
        lastRefreshedAt={live.lastRefreshedAt}
        onRetry={live.refreshNow}
      />
      <div className="page-header">
        <div>
          <h1>People</h1>
          <p className="page-subtitle">Person-centric investigation — Who is involved?</p>
          <div className="cl-hierarchy">Case → People → Relationships → Evidence → Explanation → Action</div>
          <div className="case-header-stats" style={{ marginTop: "8px" }}>
            <span>{people.length} people</span>
            <span>·</span>
            <span>Evidence-grounded, no invented connections</span>
            <span>·</span>
            <span className={`badge badge-${roleBadge.tone}`}>{roleBadge.label}</span>
            {caseParam && <><span>·</span><span>Case: {caseParam}</span></>}
          </div>
        </div>
      </div>

      {focusParam && (
        <div className="focus-indicator" style={{ marginBottom: "12px", padding: "8px", background: "var(--info-bg)", border: "1px solid var(--info-border)", borderRadius: "6px", fontSize: "12px", display: "flex", gap: "8px", alignItems: "center", flexWrap: "wrap" }}>
          <span>Focused: <strong>{focusParam.slice(0, 12)}</strong></span>
          <button className="cl-btn cl-btn-sm" onClick={() => navigate(`/relationships?case=${caseParam || ""}&focus=${focusParam}`)}>View Relationships — What connects them?</button>
          <button className="cl-btn cl-btn-sm" onClick={() => navigate(`/evidence?case=${caseParam || ""}&focus=${focusParam}`)}>View Evidence — What supports?</button>
        </div>
      )}

      <PersonList
        persons={people}
        onInvestigate={investigator ? (id) => navigate(`/investigate?case=${caseParam || ""}&focus=${id}`) : undefined}
        onView={(id) => navigate(`/people?case=${caseParam || ""}&focus=${id}`)}
      />

      <WhatNext actions={[
        { icon: "🔗", title: "Review relationships", description: "Understand how people are connected.", to: `/relationships?case=${caseParam || ""}` },
        { icon: "📄", title: "Review evidence", description: "Inspect supporting source records.", to: `/evidence?case=${caseParam || ""}` },
        { icon: "🕒", title: "Examine timeline", description: "Understand the sequence of events.", to: `/timeline?case=${caseParam || ""}` },
        { icon: "🧭", title: "Continue investigation", description: "Open the evidence-first workspace.", to: `/investigate?case=${caseParam || ""}` },
      ]} />

      <div style={{ marginTop: "16px", fontSize: "10px", fontFamily: "var(--font-mono)", color: "var(--muted)" }}>
        Mode: PERSON → PERSON only · Supporting as evidence · No demo data · Based only on evidence shown · {roleBadge.label}
      </div>
    </div>
  );
}
