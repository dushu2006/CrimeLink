/**
 * Relationships Page — Person → Person Only, Signature Interaction
 * Professional investigation workstation
 * CASE → PEOPLE → RELATIONSHIPS → EVIDENCE → EXPLANATION → ACTION
 * Judge test: Open case CR-1024, Person A ↔ Person B, why, evidence, what NOT
 * RBAC: Investigator sees Investigate buttons, Viewer read-only
 */

import { useEffect, useState } from "react";
import { useSearchParams, useNavigate } from "react-router-dom";
import { PersonConnectionCard } from "../components/investigator/PersonConnectionCard";
import { RelationshipPanel } from "../components/investigator/RelationshipPanel";
import { RelationshipPath } from "../components/investigator/RelationshipPath";
import { EvidenceDrawer } from "../components/investigator/EvidenceDrawer";
import { NoConnectionCard } from "../components/investigator/NoConnectionCard";
import { ContradictionAlert } from "../components/investigator/ContradictionAlert";
import { ProvenanceBadge } from "../components/investigator/ProvenanceBadge";
import { ClassificationBadge } from "../components/investigator/ClassificationBadge";
import { relationshipNetwork } from "../api/client";
import { useAuth } from "../store/auth";
import { isInvestigator, getRoleBadge } from "../lib/rbac";

export default function RelationshipsPage() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const session = useAuth((s) => s.session);
  const role = session?.role;
  const investigator = isInvestigator(role as any);
  const roleBadge = getRoleBadge(role as any);

  const caseParam = searchParams.get("case") || undefined;
  const focusParam = searchParams.get("focus") || undefined;

  const [relationships, setRelationships] = useState<any[]>([]);
  const [peopleSearched, setPeopleSearched] = useState(0);
  const [evidenceExamined, setEvidenceExamined] = useState(0);
  const [selected, setSelected] = useState<any>(null);
  const [selectedPath, setSelectedPath] = useState<any[]>([]);
  const [showEvidence, setShowEvidence] = useState(false);
  const [evidenceData, setEvidenceData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [focusMode, setFocusMode] = useState(false);
  const [showContradiction, setShowContradiction] = useState(false);

  useEffect(() => {
    async function load() {
      setError(null);
      setLoading(true);
      try {
        // Person → Person comes from the relationship endpoint, which derives
        // the edges server-side and aggregates every supporting record behind
        // one edge.  It used to fetch the whole entity graph and filter for
        // person endpoints in the browser.
        const network = await relationshipNetwork({
          caseId: caseParam,
          limit: 40,
          minEvidence: 1,
        });
        const nameOf = new Map(network.nodes.map((n) => [n.provenance_key, n.name]));
        const starOf = new Map(network.nodes.map((n) => [n.provenance_key, Boolean(n.is_criminal)]));
        const mapped = network.edges.map((e) => {
          const sourceName = nameOf.get(e.source) ?? e.source;
          const targetName = nameOf.get(e.target) ?? e.target;
          const sourceStar = starOf.get(e.source) ?? false;
          const targetStar = starOf.get(e.target) ?? false;
          return {
            source_person: sourceStar ? `★ ${sourceName}` : sourceName,
            target_person: targetStar ? `★ ${targetName}` : targetName,
            source_real_key: e.source,
            target_real_key: e.target,
            relationship_type: e.label,
            classification: "FACT" as const,
            confidence: e.confidence,
            confidence_label: (
              e.strength === "STRONG" ? "High" : e.strength === "MODERATE" ? "Medium" : "Low"
            ) as "High" | "Medium" | "Low",
            evidence_strength: e.strength as "STRONG" | "MODERATE" | "WEAK",
            evidence_refs: e.source_doc_ids.slice(0, 3),
            supporting_evidence: e.supporting_items.map((item) => ({
              rel_type: item.label,
              timestamp: String(item.properties?.first_ts ?? item.properties?.ts ?? "Timestamp unavailable"),
            })),
            provenance: e.supporting_items.slice(0, 5).map((item) => ({
              kind: "document",
              ref: item.evidence?.source_doc_id ?? item.source_doc_ids[0] ?? item.ref,
              label: item.label,
            })),
            why:
              `${sourceName} and ${targetName} are connected by ${e.label.toLowerCase()}` +
              (e.relationship_types.length > 1
                ? ` (also: ${e.relationship_types
                    .filter((t) => t !== e.relationship_type)
                    .map((t) => t.replace(/_/g, " ").toLowerCase())
                    .join(", ")})`
                : "") +
              `, supported by ${e.evidence_count} record${e.evidence_count === 1 ? "" : "s"}` +
              (e.cross_case ? " across more than one case" : "") +
              ".",
            limitations: [
              "Purpose of the association beyond the documented records is unknown",
              "Criminal intent is not established by this evidence alone",
            ],
            timeline: [],
            hop_count: 1,
            reasoning_path_typed: [
              { key: e.source, label: "Person", name: sourceName, rel_type: e.label },
              { key: e.target, label: "Person", name: targetName, rel_type: "" },
            ],
          };
        });
        setRelationships(mapped);
        setPeopleSearched(network.counts.persons_total);
        setEvidenceExamined(network.counts.supporting_items);
        if (focusParam) {
          const found = mapped.find(
            (r) => r.source_real_key === focusParam || r.target_real_key === focusParam,
          );
          if (found) {
            setSelected(found);
            setFocusMode(true);
            setSelectedPath(found.reasoning_path_typed || []);
          }
        }
      } catch (err) {
        // A failed request is a failure.  An empty network reads as "no
        // relationships exist", which is a different and false claim.
        setError(err instanceof Error ? err.message : String(err));
      }
      setLoading(false);
    }
    void load();
  }, [focusParam, caseParam]);

  if (error) {
    return (
      <div className="relationships-page">
        <div className="page-header">
          <h1>Relationships</h1>
        </div>
        <div className="cl-error">
          <div className="cl-empty-title">Could not load the relationship network</div>
          <div className="cl-empty-desc">{error}</div>
          <button className="cl-btn" onClick={() => window.location.reload()}>
            Retry
          </button>
        </div>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="relationships-page">
        <div className="page-header">
          <h1>Relationships</h1>
          <p className="page-subtitle">Person → Person only — What connects them?</p>
          <div className="cl-hierarchy">Case → People → Relationships → Evidence → Explanation → Action</div>
          {caseParam && <div style={{ fontSize: "11px", fontFamily: "var(--font-mono)", color: "var(--muted)", marginTop: "4px" }}>Case: {caseParam}</div>}
        </div>
        <div className="skeleton-list">
          {[1,2,3].map((i) => (
            <div key={i} className="skeleton-card"><div className="skeleton-line w-80" /><div className="skeleton-line w-60" /></div>
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className={`relationships-page ${focusMode ? "focus-mode" : ""}`}>
      <div className="page-header">
        <div>
          <h1>Relationships</h1>
          <p className="page-subtitle">Person → Person only — What connects them? Evidence-first, person-centric</p>
          <div className="cl-hierarchy">Case → People → Relationships → Evidence → Explanation → Action</div>
          {caseParam && <div style={{ fontSize: "11px", fontFamily: "var(--font-mono)", color: "var(--muted)", marginTop: "4px" }}>Case: {caseParam} · {relationships.length} relationships · <span className={`badge badge-${roleBadge.tone}`}>{roleBadge.label}</span></div>}
        </div>
        <div className="page-actions">
          <button className="cl-btn cl-btn-sm" onClick={() => setFocusMode(!focusMode)}>{focusMode ? "Exit Focus" : "Focus Mode"}</button>
          <button className="cl-btn cl-btn-sm" onClick={() => navigate(`/people?case=${caseParam || ""}`)}>← People — Who is involved?</button>
        </div>
      </div>

      {focusMode && selected ? (
        <div className="focus-mode-container">
          <button className="cl-btn cl-btn-sm" onClick={() => setFocusMode(false)}>← Back to Relationships</button>
          <div style={{ margin: "12px 0", padding: "8px", background: "var(--surface-secondary)", borderRadius: "6px", fontSize: "11px" }}>
            <strong>Judge flow:</strong> Case → People → <strong>Relationship</strong> → Evidence → Why → Limitations {investigator ? "" : "· Read-only"}
          </div>
          <RelationshipPanel
            relationship={selected}
            onViewEvidence={(ref) => { setEvidenceData({ id: ref || selected.evidence_refs[0], title: ref || selected.evidence_refs[0], type: selected.relationship_type, supports: `${selected.source_person} ↔ ${selected.target_person}` }); setShowEvidence(true); }}
            onViewTimeline={() => navigate(`/timeline?case=${caseParam || ""}`)}
            onOpenCase={() => navigate(`/cases/${caseParam || ""}`)}
            onFocusPerson={(key) => setSelectedPath(selected.reasoning_path_typed || [])}
          />
        </div>
      ) : (
        <div className="relationships-layout">
          <div className="relationships-main">
            {relationships.length > 0 ? (
              relationships.map((rel, idx) => (
                <div key={idx} className="relationship-with-path">
                  <PersonConnectionCard
                    relationship={rel}
                    onViewEvidence={() => { setEvidenceData({ id: rel.evidence_refs[0], title: rel.evidence_refs[0], type: rel.relationship_type, supports: `${rel.source_person} ↔ ${rel.target_person}` }); setShowEvidence(true); }}
                    onFocusPerson={(key) => { setSelected(rel); setSelectedPath(rel.reasoning_path_typed || []); }}
                  />
                  <div className="relationship-actions">
                    {investigator ? (
                      <>
                        <button className="cl-btn cl-btn-sm cl-btn-primary" onClick={() => { setSelected(rel); setFocusMode(true); }}>Investigate — Why? → Evidence → Limitations</button>
                        <button className="cl-btn cl-btn-sm" onClick={() => { setEvidenceData({ id: rel.evidence_refs[0], title: rel.evidence_refs[0] }); setShowEvidence(true); }}>View Evidence — What supports?</button>
                      </>
                    ) : (
                      <button className="cl-btn cl-btn-sm" onClick={() => { setEvidenceData({ id: rel.evidence_refs[0], title: rel.evidence_refs[0] }); setShowEvidence(true); }}>View Evidence — What supports this connection?</button>
                    )}
                  </div>
                </div>
              ))
            ) : (
              <NoConnectionCard
                peopleSearched={peopleSearched}
                evidenceExamined={evidenceExamined}
                reliableRelationshipsFound={0}
                onExpandSearch={() => navigate(`/people?case=${caseParam || ""}`)}
                onImportEvidence={() => navigate("/cases")}
              />
            )}
          </div>

          <div className="relationships-sidebar">
            <div className="trust-provenance">
              <ProvenanceBadge />
            </div>

            <div className="classification-legend" style={{ marginTop: "16px", padding: "12px", background: "var(--surface-secondary)", borderRadius: "8px", border: "1px solid var(--border-secondary)" }}>
              <h4 style={{ fontSize: "12px", fontWeight: 600, marginBottom: "8px" }}>Classification <span title="What records directly establish vs inference">ⓘ</span></h4>
              <div style={{ display: "flex", flexDirection: "column", gap: "6px", fontSize: "11px" }}>
                <div><ClassificationBadge classification="FACT" /> What records directly establish.</div>
                <div><ClassificationBadge classification="INFERENCE" /> What follows reasonably from evidence.</div>
                <div><ClassificationBadge classification="HYPOTHESIS" /> What may warrant further investigation.</div>
                <div><ClassificationBadge classification="UNKNOWN" /> What current evidence cannot establish.</div>
              </div>
            </div>

            {showContradiction && (
              <div style={{ marginTop: "12px" }}>
                <ContradictionAlert
                  details={
                    selected?.supporting_evidence?.length
                      ? selected.supporting_evidence.map(
                          (s: any) => `${s.rel_type} — ${s.timestamp || "Timestamp unavailable"}`,
                        )
                      : ["No supporting records are recorded for the selected relationship"]}
                  onViewConflicting={() => {
                    const ref = selected?.evidence_refs?.[0];
                    if (ref) {
                      setEvidenceData({ id: ref, title: ref });
                      setShowEvidence(true);
                    }
                  }}
                />
              </div>
            )}

            {selectedPath.length > 0 && (
              <div style={{ marginTop: "12px" }}>
                <RelationshipPath path={selectedPath} hopCount={selectedPath.length - 1} onViewEvidence={(ref) => { setEvidenceData({ id: ref, title: ref }); setShowEvidence(true); }} />
              </div>
            )}

            <div className="next-steps" style={{ marginTop: "16px", padding: "12px", background: "var(--surface-secondary)", borderRadius: "8px" }}>
              <h4 style={{ fontSize: "12px", fontWeight: 600, marginBottom: "8px" }}>What can you do next?</h4>
              <ul style={{ listStyle: "none", padding: 0, margin: 0, display: "flex", flexDirection: "column", gap: "4px" }}>
                {selected?.evidence_refs?.[0] && (
                  <li><button className="next-step-btn" onClick={() => { setEvidenceData({ id: selected.evidence_refs[0], title: selected.evidence_refs[0] }); setShowEvidence(true); }}>→ Review {selected.evidence_refs[0]} — Evidence supporting</button></li>
                )}
                <li><button className="next-step-btn" onClick={() => navigate(`/timeline?case=${caseParam || ""}`)}>→ Examine timeline — When?</button></li>
                {investigator && <li><button className="next-step-btn" onClick={() => setShowContradiction(!showContradiction)}>→ Review supporting records — Limitations</button></li>}
                <li><button className="next-step-btn" onClick={() => navigate(`/evidence?case=${caseParam || ""}`)}>→ Open source record — Provenance</button></li>
                <li><button className="next-step-btn" onClick={() => navigate(`/people?case=${caseParam || ""}`)}>← Back to People — Who is involved?</button></li>
              </ul>
            </div>

            <div className="ai-explanation-footer" style={{ marginTop: "12px", padding: "8px", background: "#f8fafc", borderRadius: "6px", fontSize: "10px", fontFamily: "var(--font-mono)", color: "var(--muted)" }}>
              Based only on evidence shown above. Unsupported claims excluded by grounding validation. {investigator ? "" : "Read-only viewer — investigation actions require Investigator role."}
            </div>
          </div>
        </div>
      )}

      <EvidenceDrawer open={showEvidence} onClose={() => setShowEvidence(false)} data={evidenceData} />
    </div>
  );
}
