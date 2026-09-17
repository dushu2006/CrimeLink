/**
 * Case Workspace — Hero Screen
 * Investigation Launchpad, not a statistics dashboard
 * CASE → PEOPLE → RELATIONSHIPS → EVIDENCE → EXPLANATION → ACTION
 * RBAC: Viewer sees read-only, Investigator sees full workflow
 *
 * Every number and every card on this page is scoped to `caseId` and comes
 * from a stored record.  It used to pull the whole 575-node master graph —
 * ignoring the case entirely — and then invent the fields a card needs: a
 * defaulted confidence, a confidence label and evidence strength hardcoded to
 * a single value, a made-up evidence reference, a made-up document id, and
 * evidence ids generated from the loop index.  Opening one of those cards
 * could never resolve provenance, because the id existed nowhere in the data.
 * The header's status and last-activity strings were literals as well.
 */

import { useCallback, useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { CaseHeader } from "../components/investigator/CaseHeader";
import { PersonConnectionCard } from "../components/investigator/PersonConnectionCard";
import { NoConnectionCard } from "../components/investigator/NoConnectionCard";
import { EvidenceDrawer } from "../components/investigator/EvidenceDrawer";
import {
  caseDashboard,
  evidenceDocuments,
  relationshipNetwork,
  type CaseDashboard,
  type EvidenceDocumentRow,
  type RelationshipNetworkResult,
} from "../api/client";
import { useAuth } from "../store/auth";
import { isInvestigator, getRoleBadge } from "../lib/rbac";
import { classifyRelationship } from "../lib/classification";

export default function CaseWorkspace() {
  const { caseId } = useParams();
  const navigate = useNavigate();
  const session = useAuth((s) => s.session);
  const role = session?.role;
  const investigator = isInvestigator(role as any);
  const roleBadge = getRoleBadge(role as any);

  const [dashboard, setDashboard] = useState<CaseDashboard | null>(null);
  const [network, setNetwork] = useState<RelationshipNetworkResult | null>(null);
  const [documents, setDocuments] = useState<EvidenceDocumentRow[]>([]);
  const [docTotal, setDocTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showEvidence, setShowEvidence] = useState(false);
  const [evidenceDocId, setEvidenceDocId] = useState<string | null>(null);

  const load = useCallback(() => {
    if (!caseId) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    // The three sections are independent, but one failure must not be hidden
    // by the others succeeding — so they settle together and report together.
    Promise.all([
      caseDashboard(caseId),
      relationshipNetwork({ caseId, limit: 40, minEvidence: 1 }),
      evidenceDocuments({ caseId, limit: 6 }),
    ])
      .then(([dash, net, docs]) => {
        setDashboard(dash);
        setNetwork(net);
        setDocuments(docs.items);
        setDocTotal(docs.total);
      })
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoading(false));
  }, [caseId]);

  useEffect(load, [load]);

  const people = network?.nodes ?? [];
  const nameOf = new Map(people.map((n) => [n.provenance_key, n.name]));
  const starOf = new Map(people.map((n) => [n.provenance_key, Boolean(n.is_criminal)]));

  const relationships = (network?.edges ?? []).map((e) => {
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
      // Derived from the edge, not stamped: a weak relationship is not a fact.
      classification: classifyRelationship({
        strength: e.strength,
        confidence: e.confidence,
        supportingCount: e.supporting_items.length,
      }),

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

  if (error) {
    return (
      <div className="case-workspace">
        <div className="cl-error">
          <div className="cl-empty-title">Could not load this case</div>
          <div className="cl-empty-desc">{error}</div>
          <button className="cl-btn" onClick={load}>
            Retry
          </button>
        </div>
      </div>
    );
  }

  if (!caseId || !dashboard) {
    return (
      <div className="case-workspace">
        <div className="cl-empty">
          <div className="cl-empty-title">No case selected</div>
          <div className="cl-empty-desc">Open a case from the Cases registry.</div>
        </div>
      </div>
    );
  }

  const { header, stats } = dashboard;

  return (
    <div className="case-workspace">
      <CaseHeader
        caseId={header.case_number}
        status={header.status}
        peopleCount={stats.entities_by_label?.Person ?? people.length}
        relationshipsCount={network?.counts.relationships_total ?? relationships.length}
        evidenceCount={stats.evidence}
        lastActivity={
          header.updated_at ? new Date(header.updated_at).toLocaleString() : "No recorded activity"
        }
        onContinue={investigator ? () => navigate(`/investigate?case=${caseId}`) : undefined}
      />
      <div
        style={{
          fontSize: "11px",
          fontFamily: "var(--font-mono)",
          color: "var(--muted)",
          marginBottom: "12px",
        }}
      >
        <span className={`badge badge-${roleBadge.tone}`}>{roleBadge.label}</span>{" "}
        {investigator
          ? "· Investigate & review"
          : "· Read-only viewer — Case → People → Relationships → Evidence → Timeline"}
      </div>

      <div className="case-workspace-section">
        <div className="section-header">
          <h2>PEOPLE</h2>
          <span className="section-count">
            {network?.counts.persons_total ?? people.length} persons
          </span>
          <button className="cl-btn cl-btn-sm" onClick={() => navigate(`/people?case=${caseId}`)}>
            View All People
          </button>
        </div>
        {people.length > 0 ? (
          <div className="people-pills">
            {people.slice(0, 6).map((p) => (
              <button
                key={p.provenance_key}
                className="person-pill"
                onClick={() => navigate(`/people?case=${caseId}&focus=${p.provenance_key}`)}
              >
                <span className="person-pill-icon">{p.is_criminal ? "★" : "👤"}</span>
                <span>{p.name || p.provenance_key.slice(0, 12)}</span>
              </button>
            ))}
          </div>
        ) : (
          <div className="cl-empty">
            <div className="cl-empty-title">No people yet</div>
            <div className="cl-empty-desc">Import evidence to identify people.</div>
          </div>
        )}
      </div>

      <div className="case-workspace-section">
        <div className="section-header">
          <h2>KEY RELATIONSHIPS</h2>
          <span className="section-count">
            {network?.counts.relationships_total ?? relationships.length} relationships
          </span>
          <button
            className="cl-btn cl-btn-sm"
            onClick={() => navigate(`/relationships?case=${caseId}`)}
          >
            View All Relationships
          </button>
        </div>
        {relationships.length > 0 ? (
          <div className="relationships-grid">
            {relationships.slice(0, 4).map((rel, idx) => (
              <PersonConnectionCard
                key={idx}
                relationship={rel}
                onViewEvidence={() => {
                  const docId = rel.evidence_refs[0];
                  if (!docId) return;
                  setEvidenceDocId(docId);
                  setShowEvidence(true);
                }}
              />
            ))}
          </div>
        ) : (
          <NoConnectionCard
            peopleSearched={network?.counts.persons_total ?? 0}
            evidenceExamined={network?.counts.supporting_items ?? 0}
            reliableRelationshipsFound={0}
            onExpandSearch={() => navigate(`/investigate?case=${caseId}`)}
            onImportEvidence={() => navigate("/cases")}
          />
        )}
      </div>

      <div className="case-workspace-section">
        <div className="section-header">
          <h2>RECENT EVIDENCE</h2>
          <span className="section-count">{docTotal} records</span>
          <button className="cl-btn cl-btn-sm" onClick={() => navigate(`/evidence?case=${caseId}`)}>
            View All Evidence
          </button>
        </div>
        {documents.length > 0 ? (
          <div className="evidence-list">
            {documents.map((doc) => (
              <button
                key={doc.id}
                className="evidence-item"
                onClick={() => {
                  setEvidenceDocId(doc.id);
                  setShowEvidence(true);
                }}
              >
                <span className="evidence-id">{doc.filename}</span>
                <span className="evidence-type">{doc.document_type.replace(/_/g, " ")}</span>
              </button>
            ))}
          </div>
        ) : (
          <div className="cl-empty">
            <div className="cl-empty-title">No evidence yet</div>
            <div className="cl-empty-desc">Evidence will appear here once imported.</div>
          </div>
        )}
      </div>

      <div className="case-workspace-section">
        <div className="section-header">
          <h2>What next?</h2>
        </div>
        <div className="next-steps">
          {investigator && (
            <button
              className="next-step-item"
              onClick={() => navigate(`/investigate?case=${caseId}`)}
            >
              Continue Investigation — Investigate Relationship
            </button>
          )}
          <button className="next-step-item" onClick={() => navigate(`/people?case=${caseId}`)}>
            Review People — Who is involved?
          </button>
          <button
            className="next-step-item"
            onClick={() => navigate(`/relationships?case=${caseId}`)}
          >
            Review Relationships — What connects them?
          </button>
          <button className="next-step-item" onClick={() => navigate(`/timeline?case=${caseId}`)}>
            Examine Timeline — When?
          </button>
          <button className="next-step-item" onClick={() => navigate(`/evidence?case=${caseId}`)}>
            Review Evidence — What supports?
          </button>
        </div>
      </div>

      <div
        style={{
          marginTop: "16px",
          fontSize: "10px",
          fontFamily: "var(--font-mono)",
          color: "var(--muted)",
        }}
      >
        Mode: PERSON → PERSON only · Supporting entities as evidence · Scoped to {header.case_number}{" "}
        · {roleBadge.label}
      </div>

      <EvidenceDrawer
        open={showEvidence}
        onClose={() => setShowEvidence(false)}
        data={evidenceDocId ? { id: evidenceDocId } : null}
      />
    </div>
  );
}
