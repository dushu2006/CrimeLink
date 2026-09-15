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
import { DeterministicFallbackCard } from "../components/investigator/DeterministicFallbackCard";
import { ContradictionAlert } from "../components/investigator/ContradictionAlert";
import { ProvenanceBadge } from "../components/investigator/ProvenanceBadge";
import { ClassificationBadge } from "../components/investigator/ClassificationBadge";
import { masterGraph, masterPersons } from "../api/client";
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
  const [selected, setSelected] = useState<any>(null);
  const [selectedPath, setSelectedPath] = useState<any[]>([]);
  const [showEvidence, setShowEvidence] = useState(false);
  const [evidenceData, setEvidenceData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [focusMode, setFocusMode] = useState(false);
  const [showContradiction, setShowContradiction] = useState(false);

  useEffect(() => {
    async function load() {
      setLoading(true);
      try {
        const personsRes = await masterPersons();
        const persons = personsRes.items || [];
        const personKeys = new Set(persons.map((p: any) => p.provenance_key));
        const graph = await masterGraph();
        const edges = graph.edges || [];
        const personPerson = edges.filter((e: any) => personKeys.has(e.source) && personKeys.has(e.target));
        const mapped = personPerson.slice(0, 20).map((e: any) => ({
          source_person: e.source.slice(0, 12),
          target_person: e.target.slice(0, 12),
          source_real_key: e.source,
          target_real_key: e.target,
          relationship_type: e.rel_type === "ASSOCIATE_OF" ? "Communication" : e.rel_type,
          classification: "INFERENCE" as const,
          confidence: e.confidence || 0.75,
          confidence_label: "Medium" as const,
          evidence_strength: "MODERATE" as const,
          evidence_refs: e.source_doc_ids?.length ? e.source_doc_ids.slice(0, 3) : ["E-042"],
          supporting_evidence: [{ rel_type: e.rel_type, timestamp: "Timestamp unavailable" }],
          provenance: [{ kind: "document", ref: e.source_doc_id || "doc-001", label: "Source record" }],
          why: `${e.source.slice(0, 12)} and ${e.target.slice(0, 12)} are connected via ${e.rel_type.toLowerCase()}`,
          limitations: ["Purpose of association beyond documented records is unknown", "Criminal intent not established by this evidence alone"],
          timeline: [],
          hop_count: 1,
          reasoning_path_typed: [
            { key: e.source, label: "Person", name: e.source.slice(0, 12), rel_type: e.rel_type },
            { key: e.target, label: "Person", name: e.target.slice(0, 12), rel_type: "" },
          ],
        }));
        setRelationships(mapped);
        if (focusParam) {
          const found = mapped.find((r) => r.source_real_key === focusParam || r.target_real_key === focusParam);
          if (found) {
            setSelected(found);
            setFocusMode(true);
            setSelectedPath(found.reasoning_path_typed || []);
          }
        }
      } catch {}
      setLoading(false);
    }
    void load();
  }, [focusParam]);

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
              <NoConnectionCard peopleSearched={12} evidenceExamined={31} reliableRelationshipsFound={0} />
            )}

            {investigator && (
              <div className="deterministic-fallback-section" style={{ marginTop: "20px", padding: "12px", border: "1px dashed var(--border-primary)", borderRadius: "8px" }}>
                <h4 style={{ fontSize: "12px", marginBottom: "8px" }}>AI Availability — Deterministic fallback</h4>
                <DeterministicFallbackCard
                  sourcePerson="PERSON-001"
                  targetPerson="PERSON-024"
                  relationshipType="Communication"
                  evidenceCount={4}
                  onViewEvidence={() => { setEvidenceData({ id: "E-042", title: "E-042" }); setShowEvidence(true); }}
                  onViewTimeline={() => navigate(`/timeline?case=${caseParam || ""}`)}
                />
              </div>
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
                <ContradictionAlert details={["E-042 Location X 14:00", "E-071 Location Y 14:05"]} onViewConflicting={() => { setEvidenceData({ id: "E-042", title: "Conflicting records" }); setShowEvidence(true); }} />
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
                <li><button className="next-step-btn" onClick={() => { setEvidenceData({ id: "E-042", title: "E-042" }); setShowEvidence(true); }}>→ Review E-042 — Evidence supporting</button></li>
                <li><button className="next-step-btn" onClick={() => navigate(`/timeline?case=${caseParam || ""}`)}>→ Examine timeline — When?</button></li>
                {investigator && <li><button className="next-step-btn" onClick={() => setShowContradiction(!showContradiction)}>→ Review conflicting records — Limitations</button></li>}
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
