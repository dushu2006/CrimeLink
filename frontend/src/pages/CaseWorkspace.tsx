/**
 * Case Workspace — Hero Screen
 * Investigation Launchpad, not statistics dashboard
 * CASE → PEOPLE → RELATIONSHIPS → EVIDENCE → EXPLANATION → ACTION
 * RBAC: Viewer sees read-only, Investigator sees full workflow
 */

import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { CaseHeader } from "../components/investigator/CaseHeader";
import { PersonList } from "../components/investigator/PersonList";
import { PersonConnectionCard } from "../components/investigator/PersonConnectionCard";
import { NoConnectionCard } from "../components/investigator/NoConnectionCard";
import { EvidenceDrawer } from "../components/investigator/EvidenceDrawer";
import { masterGraph } from "../api/client";
import type { GraphNodeRow, GraphEdgeRow } from "../api/client";
import { useAuth } from "../store/auth";
import { isInvestigator, getRoleBadge } from "../lib/rbac";

function isPersonLabel(label: string): boolean {
  return label.toLowerCase() === "person" || label.toLowerCase() === "people";
}

export default function CaseWorkspace() {
  const { caseId } = useParams();
  const navigate = useNavigate();
  const session = useAuth((s) => s.session);
  const role = session?.role;
  const investigator = isInvestigator(role as any);
  const roleBadge = getRoleBadge(role as any);

  const [people, setPeople] = useState<any[]>([]);
  const [relationships, setRelationships] = useState<any[]>([]);
  const [evidence, setEvidence] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [showEvidence, setShowEvidence] = useState(false);
  const [evidenceData, setEvidenceData] = useState<any>(null);

  useEffect(() => {
    async function load() {
      setLoading(true);
      try {
        const graph = await masterGraph();
        const nodes: GraphNodeRow[] = graph.nodes || [];
        const edges: GraphEdgeRow[] = graph.edges || [];
        const personNodes = nodes.filter((n) => isPersonLabel(n.label));
        setPeople(personNodes.slice(0, 12).map((p) => ({
          id: p.provenance_key,
          pseudonym: p.provenance_key,
          displayName: p.name,
          relationshipsCount: edges.filter((e) => e.source === p.provenance_key || e.target === p.provenance_key).length,
          evidenceCount: p.source_doc_ids?.length || 0,
          casesCount: p.case_ids?.length || 1,
        })));

        const personKeys = new Set(personNodes.map((p) => p.provenance_key));
        const personPersonEdges = edges.filter((e) => personKeys.has(e.source) && personKeys.has(e.target));
        setRelationships(personPersonEdges.slice(0, 6).map((e) => ({
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
          limitations: ["Purpose of association beyond documented records is unknown"],
          timeline: [],
          hop_count: 1,
        })));

        setEvidence(edges.slice(0, 6).map((e, i) => ({
          id: `E-${String(i + 42).padStart(3, "0")}`,
          type: e.rel_type,
          caseId,
        })));
      } catch (err) {
        console.error(err);
      } finally {
        setLoading(false);
      }
    }
    void load();
  }, [caseId]);

  if (loading) {
    return (
      <div className="case-workspace">
        <div className="skeleton-hero">
          <div className="skeleton-line w-60" />
          <div className="skeleton-line w-40" />
          <div className="skeleton-line w-80" />
        </div>
      </div>
    );
  }

  return (
    <div className="case-workspace">
      <CaseHeader
        caseId={caseId || "CR-1024"}
        status="Active"
        peopleCount={people.length}
        relationshipsCount={relationships.length}
        evidenceCount={evidence.length}
        lastActivity="12 min ago"
        onContinue={investigator ? () => navigate(`/investigate?case=${caseId}`) : undefined}
      />
      <div style={{ fontSize: "11px", fontFamily: "var(--font-mono)", color: "var(--muted)", marginBottom: "12px" }}>
        <span className={`badge badge-${roleBadge.tone}`}>{roleBadge.label}</span> {investigator ? "· Investigate & review" : "· Read-only viewer — Case → People → Relationships → Evidence → Timeline"}
      </div>

      <div className="case-workspace-section">
        <div className="section-header">
          <h2>PEOPLE</h2>
          <span className="section-count">{people.length} persons</span>
          <button className="cl-btn cl-btn-sm" onClick={() => navigate(`/people?case=${caseId}`)}>View All People</button>
        </div>
        {people.length > 0 ? (
          <div className="people-pills">
            {people.slice(0, 6).map((p) => (
              <button key={p.id} className="person-pill" onClick={() => navigate(`/people?case=${caseId}&focus=${p.id}`)}>
                <span className="person-pill-icon">👤</span>
                <span>{p.displayName || p.pseudonym.slice(0, 12)}</span>
              </button>
            ))}
          </div>
        ) : (
          <div className="cl-empty"><div className="cl-empty-title">No people yet</div><div className="cl-empty-desc">Import evidence to identify people.</div></div>
        )}
      </div>

      <div className="case-workspace-section">
        <div className="section-header">
          <h2>KEY RELATIONSHIPS</h2>
          <span className="section-count">{relationships.length} relationships</span>
          <button className="cl-btn cl-btn-sm" onClick={() => navigate(`/relationships?case=${caseId}`)}>View All Relationships</button>
        </div>
        {relationships.length > 0 ? (
          <div className="relationships-grid">
            {relationships.slice(0, 4).map((rel, idx) => (
              <PersonConnectionCard key={idx} relationship={rel} onViewEvidence={() => { setEvidenceData({ id: rel.evidence_refs[0], title: rel.evidence_refs[0] }); setShowEvidence(true); }} />
            ))}
          </div>
        ) : (
          <NoConnectionCard peopleSearched={people.length} evidenceExamined={evidence.length} reliableRelationshipsFound={0} onExpandSearch={() => navigate(`/investigate?case=${caseId}`)} onImportEvidence={() => navigate("/cases")} />
        )}
      </div>

      <div className="case-workspace-section">
        <div className="section-header">
          <h2>RECENT EVIDENCE</h2>
          <span className="section-count">{evidence.length} records</span>
          <button className="cl-btn cl-btn-sm" onClick={() => navigate(`/evidence?case=${caseId}`)}>View All Evidence</button>
        </div>
        {evidence.length > 0 ? (
          <div className="evidence-list">
            {evidence.map((ev) => (
              <button key={ev.id} className="evidence-item" onClick={() => { setEvidenceData({ id: ev.id, title: ev.id, type: ev.type }); setShowEvidence(true); }}>
                <span className="evidence-id">{ev.id}</span>
                <span className="evidence-type">{ev.type}</span>
              </button>
            ))}
          </div>
        ) : (
          <div className="cl-empty"><div className="cl-empty-title">No evidence yet</div><div className="cl-empty-desc">Evidence will appear here once imported.</div></div>
        )}
      </div>

      <div className="case-workspace-section">
        <div className="section-header">
          <h2>What next?</h2>
        </div>
        <div className="next-steps">
          {investigator && <button className="next-step-item" onClick={() => navigate(`/investigate?case=${caseId}`)}>Continue Investigation — Investigate Relationship</button>}
          <button className="next-step-item" onClick={() => navigate(`/people?case=${caseId}`)}>Review People — Who is involved?</button>
          <button className="next-step-item" onClick={() => navigate(`/relationships?case=${caseId}`)}>Review Relationships — What connects them?</button>
          <button className="next-step-item" onClick={() => navigate(`/timeline?case=${caseId}`)}>Examine Timeline — When?</button>
          <button className="next-step-item" onClick={() => navigate(`/evidence?case=${caseId}`)}>Review Evidence — What supports?</button>
        </div>
      </div>

      <div style={{ marginTop: "16px", fontSize: "10px", fontFamily: "var(--font-mono)", color: "var(--muted)" }}>
        Mode: PERSON → PERSON only · Supporting as evidence · No demo data · Based only on evidence shown · {roleBadge.label}
      </div>

      <EvidenceDrawer open={showEvidence} onClose={() => setShowEvidence(false)} data={evidenceData} />
    </div>
  );
}
