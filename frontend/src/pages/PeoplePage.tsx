/**
 * People Page — Center of CrimeLink
 * Person-centric, calm, evidence-first
 * No fake data, no random counts
 * Judge test: Case → People → Relationship → Evidence → Why → Limitations
 * RBAC: Investigator sees Investigate, Viewer read-only
 */

import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { PersonList } from "../components/investigator/PersonList";
import { masterPersons } from "../api/client";
import { useAuth } from "../store/auth";
import { isInvestigator, getRoleBadge } from "../lib/rbac";

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

  useEffect(() => {
    async function load() {
      setLoading(true);
      try {
        // People come from the active dataset only.  This used to also pull the
        // whole 575-node entity graph just to count edges per person; the
        // person endpoint already carries the connection count.
        const personsRes = await masterPersons();
        const items = (personsRes as any).items || [];
        setPeople(items.map((p: any) => {
          const key = p.provenance_key || p.id;
          return {
            id: key,
            pseudonym: key,
            displayName: p.name || key.slice(0, 12),
            relationshipsCount: p.connections || 0,
            evidenceCount: p.source_doc_ids?.length || 0,
            casesCount: p.case_ids?.length || 0,
            role: p.role ?? null,
            criminalStatus: p.criminal_status ?? null,
            isCriminal: Boolean(p.criminal_status),
          };
        }));
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
      setLoading(false);
    }
    void load();
  }, []);

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

      <div className="investigation-actions" style={{ marginTop: "20px", padding: "12px", background: "var(--surface-secondary)", borderRadius: "8px", border: "1px solid var(--border-secondary)" }}>
        <h3 style={{ fontSize: "12px", fontWeight: 600, marginBottom: "8px" }}>What can you do next?</h3>
        <ul style={{ fontSize: "12px", display: "flex", flexDirection: "column", gap: "4px", listStyle: "none", padding: 0 }}>
          <li><button className="next-step-btn" onClick={() => navigate(`/relationships?case=${caseParam || ""}`)}>→ Review Relationships — What connects them?</button></li>
          <li><button className="next-step-btn" onClick={() => navigate(`/evidence?case=${caseParam || ""}`)}>→ Examine Evidence — What supports?</button></li>
          <li><button className="next-step-btn" onClick={() => navigate(`/timeline?case=${caseParam || ""}`)}>→ View Timeline — When did it occur?</button></li>
          {investigator ? (
            <li><button className="next-step-btn" onClick={() => navigate(`/investigate?case=${caseParam || ""}`)}>→ Continue Investigation — Explain why</button></li>
          ) : (
            <li style={{ fontSize: "11px", color: "var(--muted)", fontFamily: "var(--font-mono)" }}>Read-only viewer — investigation actions require Investigator role</li>
          )}
        </ul>
      </div>

      <div style={{ marginTop: "16px", fontSize: "10px", fontFamily: "var(--font-mono)", color: "var(--muted)" }}>
        Mode: PERSON → PERSON only · Supporting as evidence · No demo data · Based only on evidence shown · {roleBadge.label}
      </div>
    </div>
  );
}
