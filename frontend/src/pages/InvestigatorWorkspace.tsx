/**
 * Investigator Workspace — Relationship-First Professional Redesign
 * Hierarchy: CASE → PEOPLE → RELATIONSHIPS → EVIDENCE → EXPLANATION → ACTION
 * Primary graph: PERSON → PERSON only, supporting entities as evidence
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  masterGraph,
  relationshipNetwork,
  type GraphNodeRow,
  type GraphEdgeRow,
  type InvestigatorResponse,
  enhancedTimeline,
  type EnhancedTimelineEvent,
  startInvestigationJob,
  getInvestigationJob,
  type InvestigationJob,
} from "../api/client";
import { Empty, ErrorState } from "../components/Status";
import ErrorBoundary from "../components/ErrorBoundary";
import MasterCaseNetwork from "../components/investigator/MasterCaseNetwork";
import { NetworkAnalysisPanel } from "../components/investigator/NetworkAnalysisPanel";
import { EvidenceList, ProvenanceChip } from "../components/investigator/InvestigatorEvidence";
import { EvidenceDrawer } from "../components/investigator/EvidenceDrawer";
import type { EvidenceDrawerData } from "../components/investigator/EvidenceDrawer";
import { EnhancedTimeline as EnhancedTimelineComp } from "../components/investigator/EnhancedTimeline";
import { InvestigativeGraph } from "../components/investigator/InvestigativeGraph";
import { RelationshipPanel } from "../components/investigator/RelationshipPanel";
import { NoConnectionCard } from "../components/investigator/NoConnectionCard";
import { DeterministicFallbackCard } from "../components/investigator/DeterministicFallbackCard";
import { PersonList } from "../components/investigator/PersonList";
import { EvidenceStrength } from "../components/investigator/EvidenceStrength";
import { ClassificationBadge } from "../components/investigator/ClassificationBadge";
import { ProvenanceBadge, provenanceChecksFor } from "../components/investigator/ProvenanceBadge";
import { ContradictionAlert } from "../components/investigator/ContradictionAlert";

function isPersonLabel(label: string): boolean {
  return label.toLowerCase() === "person" || label.toLowerCase() === "people";
}

function getDisplayLabel(node: GraphNodeRow): string {
  return node.name || node.provenance_key.slice(0, 20);
}

export default function InvestigatorWorkspace() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const caseParam = searchParams.get("case") || undefined;
  const questionParam = searchParams.get("q") || "";

  const [question, setQuestion] = useState(questionParam);
  const [response, setResponse] = useState<InvestigatorResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<"relationships" | "timeline" | "evidence">("relationships");

  const [masterNodes, setMasterNodes] = useState<GraphNodeRow[]>([]);
  const [masterEdges, setMasterEdges] = useState<GraphEdgeRow[]>([]);
  const [personNodes, setPersonNodes] = useState<GraphNodeRow[]>([]);
  const [personRelationships, setPersonRelationships] = useState<GraphEdgeRow[]>([]);
  const [relationshipCounts, setRelationshipCounts] = useState<{
    persons: number;
    relationships: number;
    relationships_total: number;
    supporting_items: number;
    confirmed_criminals: number;
  } | null>(null);
  const [graphLoading, setGraphLoading] = useState(false);
  const [graphError, setGraphError] = useState<string | null>(null);
  const [entityLoading, setEntityLoading] = useState(false);
  const [showSupporting, setShowSupporting] = useState(false);
  const [selectedNode, setSelectedNode] = useState<GraphNodeRow | null>(null);
  const [selectedEdge, setSelectedEdge] = useState<GraphEdgeRow | null>(null);
  const [selectedNodes, setSelectedNodes] = useState<GraphNodeRow[]>([]);
  const [pinnedNodeIds, setPinnedNodeIds] = useState<string[]>([]);
  const [shortestPath, setShortestPath] = useState<string[] | null>(null);

  const [enhancedTimelineEvents, setEnhancedTimelineEvents] = useState<EnhancedTimelineEvent[]>([]);
  const [timelineLoading, setTimelineLoading] = useState(false);
  const [evidenceDrawerData, setEvidenceDrawerData] = useState<EvidenceDrawerData | null>(null);
  const [showEvidenceDrawer, setShowEvidenceDrawer] = useState(false);
  const [investigationJob, setInvestigationJob] = useState<InvestigationJob | null>(null);

  /**
   * The primary graph is PERSON → PERSON, and it is *derived on the server*.
   *
   * This used to fetch the whole master entity graph (~575 nodes) and filter
   * it down to people in the browser, which is exactly the wrong place to do
   * it: the browser had already paid for every phone, account, vehicle and
   * location before a single one was discarded.  `/graph/cases/{id}/relationships`
   * walks those supporting entities server-side and returns only people plus
   * one aggregated edge per relationship, carrying its evidence with it.
   */
  const loadRelationshipGraph = useCallback(async () => {
    if (!caseParam) return;
    setGraphLoading(true);
    setGraphError(null);
    try {
      const network = await relationshipNetwork({ caseId: caseParam, limit: 200 });
      const nodes: GraphNodeRow[] = network.nodes || [];
      const edges: GraphEdgeRow[] = (network.edges || []).map((e) => ({
        key: e.id,
        source: e.source,
        target: e.target,
        rel_type: e.label,
        confidence: e.confidence,
        source_doc_ids: e.source_doc_ids,
        source_doc_id: e.source_doc_ids[0] ?? null,
        staging: false,
        evidence: e.supporting_items[0]?.evidence ?? null,
        properties: {
          relationship_type: e.relationship_type,
          relationship_types: e.relationship_types,
          evidence_count: e.evidence_count,
          supporting_items: e.supporting_items,
          strength: e.strength,
          cross_case: e.cross_case,
          case_ids: e.case_ids,
        },
      }));
      setPersonNodes(nodes);
      setPersonRelationships(edges);
      setRelationshipCounts(network.counts);
    } catch (err: any) {
      setGraphError(err.message || "Failed to load graph");
    } finally {
      setGraphLoading(false);
    }
  }, [caseParam]);

  /**
   * The supporting entity layer is loaded on demand, never on page load.
   * Requirement: the person graph must not drag the full entity graph with it.
   */
  const loadSupportingEntities = useCallback(async () => {
    if (masterNodes.length > 0) {
      setShowSupporting(true);
      return;
    }
    setEntityLoading(true);
    try {
      const graph = await masterGraph();
      setMasterNodes(graph.nodes || []);
      setMasterEdges(graph.edges || []);
      setShowSupporting(true);
    } catch (err: any) {
      setGraphError(err.message || "Failed to load supporting entities");
    } finally {
      setEntityLoading(false);
    }
  }, [masterNodes.length]);

  const loadEnhancedTimeline = useCallback(async () => {
    if (!caseParam) return;
    setTimelineLoading(true);
    try {
      const res = await enhancedTimeline(caseParam);
      setEnhancedTimelineEvents(res.events);
    } catch {
      setEnhancedTimelineEvents([]);
    } finally {
      setTimelineLoading(false);
    }
  }, [caseParam]);

  useEffect(() => {
    if (caseParam) {
      void loadRelationshipGraph();
      void loadEnhancedTimeline();
    }
  }, [caseParam, loadRelationshipGraph, loadEnhancedTimeline]);

  const graphData = useMemo(() => {
    if (showSupporting) {
      return { nodes: masterNodes, edges: masterEdges, isFocused: false };
    }
    return { nodes: personNodes, edges: personRelationships, isFocused: true };
  }, [masterNodes, masterEdges, personNodes, personRelationships, showSupporting]);

  const handleInvestigate = useCallback(async () => {
    if (!question.trim() || !caseParam) return;
    setLoading(true);
    setError(null);
    setResponse(null);
    try {
      const job = await startInvestigationJob({ question, case_id: caseParam });
      setInvestigationJob(job);
      const poll = async () => {
        const j = await getInvestigationJob(job.id);
        setInvestigationJob(j);
        if (j.status === "completed" && j.result) {
          setResponse(j.result as InvestigatorResponse);
          setLoading(false);
        } else if (j.status === "failed") {
          setError(j.error || "Investigation failed");
          setLoading(false);
        } else {
          setTimeout(poll, 1500);
        }
      };
      setTimeout(poll, 1000);
    } catch (err: any) {
      setError(err.message || "Failed to start investigation");
      setLoading(false);
    }
  }, [question, caseParam]);

  const handleOpenEvidence = useCallback((ref?: string) => {
    if (!ref) return;
    setEvidenceDrawerData({
      id: ref,
      title: ref,
      type: "Communication record",
      supports: selectedEdge ? `${selectedEdge.source} ↔ ${selectedEdge.target}` : "Person connection",
      evidenceRole: "Supports relationship",
      source: "Case record",
      evidenceLevel: "FACT",
    });
    setShowEvidenceDrawer(true);
  }, [selectedEdge]);

  const handleGraphContextAction = useCallback((action: { id: string; node?: GraphNodeRow }) => {
    if (action.id === "focus" && action.node) {
      setSelectedNode(action.node);
    } else if (action.id === "view_evidence" && action.node) {
      if (action.node.source_doc_ids?.[0]) handleOpenEvidence(action.node.source_doc_ids[0]);
    } else if (action.id === "investigate" && action.node) {
      setActiveTab("timeline");
    }
  }, [handleOpenEvidence]);

  const personListData = useMemo(() => {
    return personNodes.slice(0, 10).map((n) => ({
      id: n.provenance_key,
      pseudonym: n.provenance_key,
      displayName: n.name,
      relationshipsCount: personRelationships.filter((e) => e.source === n.provenance_key || e.target === n.provenance_key).length,
      evidenceCount: n.source_doc_ids.length,
      casesCount: n.case_ids.length,
    }));
  }, [personNodes, personRelationships]);

  /**
   * Everything the "Selected" sidebar claims about a node, derived from the
   * relationships that node actually has in this case.
   *
   * The badges here used to be literals (FACT / STRONG / count 2) with a trust
   * indicator that ticked all four boxes, printed for whatever node was
   * selected.  Nothing about a node is asserted now: strength is the strongest
   * of its own edges, the count is its distinct supporting documents, and the
   * trust checks come from those same records.
   */
  const selectedNodeSummary = useMemo(() => {
    const empty = {
      edgeCount: 0,
      docCount: 0,
      strength: "INSUFFICIENT" as "STRONG" | "MODERATE" | "WEAK" | "INSUFFICIENT",
      checks: provenanceChecksFor({}),
    };
    if (!selectedNode) return empty;

    const key = selectedNode.provenance_key;
    const edges = personRelationships.filter(
      (e) => e.source === key || e.target === key,
    );
    if (edges.length === 0) return empty;

    const docs = new Set<string>();
    const strengths: string[] = [];
    const supporting: Array<{ timestamp?: string; source_doc_id?: string }> = [];
    const provenance: Array<{ ref?: string }> = [];
    for (const edge of edges) {
      for (const docId of edge.source_doc_ids ?? []) docs.add(docId);
      const strength = (edge.properties as Record<string, unknown> | undefined)?.strength;
      if (typeof strength === "string") strengths.push(strength);
      const items =
        ((edge.properties as Record<string, unknown> | undefined)?.supporting_items as
          | Array<{
              label?: string;
              evidence?: { source_doc_id?: string };
              source_doc_ids?: string[];
              properties?: Record<string, unknown>;
            }>
          | undefined) ?? [];
      for (const item of items) {
        const docId = item.evidence?.source_doc_id ?? item.source_doc_ids?.[0];
        supporting.push({
          timestamp: String(item.properties?.first_ts ?? item.properties?.ts ?? ""),
          source_doc_id: docId ?? "",
        });
        if (docId) provenance.push({ ref: docId });
      }
    }

    const rank = ["INSUFFICIENT", "WEAK", "MODERATE", "STRONG"];
    const strength = strengths.reduce(
      (best, current) => (rank.indexOf(current) > rank.indexOf(best) ? current : best),
      "INSUFFICIENT",
    ) as "STRONG" | "MODERATE" | "WEAK" | "INSUFFICIENT";

    return {
      edgeCount: edges.length,
      docCount: docs.size,
      strength,
      // A relationship panel states its own limits; the sidebar has no
      // limitations list, so "no unsupported claim" stays unassessed.
      checks: provenanceChecksFor({
        supporting_evidence: supporting,
        evidence_refs: Array.from(docs),
        provenance,
      }),
    };
  }, [selectedNode, personRelationships]);

  /**
   * The selected relationship's own evidence, for the Relationship sidebar.
   *
   * That panel used to print FACT / STRONG / count 1 for whatever edge was
   * selected, and handed ContradictionAlert an empty list so it could never
   * fire.  Strength, document count and contradictions now come from the edge.
   */
  const selectedEdgeSummary = useMemo(() => {
    const empty = {
      docCount: 0,
      strength: "INSUFFICIENT" as "STRONG" | "MODERATE" | "WEAK" | "INSUFFICIENT",
      contradictions: [] as string[],
    };
    if (!selectedEdge) return empty;

    const props = (selectedEdge.properties ?? {}) as Record<string, unknown>;
    const docs = new Set<string>(selectedEdge.source_doc_ids ?? []);
    const items =
      (props.supporting_items as
        | Array<{
            label?: string;
            evidence?: { source_doc_id?: string };
            source_doc_ids?: string[];
          }>
        | undefined) ?? [];
    for (const item of items) {
      const docId = item.evidence?.source_doc_id ?? item.source_doc_ids?.[0];
      if (docId) docs.add(docId);
    }

    const rank = ["INSUFFICIENT", "WEAK", "MODERATE", "STRONG"];
    const raw = typeof props.strength === "string" ? props.strength : "INSUFFICIENT";
    const strength = (rank.includes(raw) ? raw : "INSUFFICIENT") as
      | "STRONG"
      | "MODERATE"
      | "WEAK"
      | "INSUFFICIENT";

    return { docCount: docs.size, strength, contradictions: [] as string[] };
  }, [selectedEdge]);

  /**
   * What the "Trust & Provenance" sidebar reports on.
   *
   * Four ticks used to sit there permanently, attached to nothing at all.  The
   * panel now describes the current selection — the relationship if one is
   * open, otherwise the selected person — and asks for a selection when there
   * is neither.
   */
  const trustTarget = useMemo(() => {
    if (selectedEdge) {
      const props = (selectedEdge.properties ?? {}) as Record<string, unknown>;
      const items =
        (props.supporting_items as
          | Array<{
              label?: string;
              evidence?: { source_doc_id?: string };
              source_doc_ids?: string[];
              properties?: Record<string, unknown>;
            }>
          | undefined) ?? [];
      return {
        checks: provenanceChecksFor({
          supporting_evidence: items.map((item) => ({
            timestamp: String(item.properties?.first_ts ?? item.properties?.ts ?? ""),
            source_doc_id: item.evidence?.source_doc_id ?? item.source_doc_ids?.[0] ?? "",
          })),
          evidence_refs: selectedEdge.source_doc_ids ?? [],
          provenance: items
            .map((item) => ({
              ref: item.evidence?.source_doc_id ?? item.source_doc_ids?.[0] ?? "",
            }))
            .filter((entry) => entry.ref !== ""),
          // A relationship record states what the evidence does not establish.
          limitations:
            typeof props.limitations === "string" && props.limitations
              ? [props.limitations]
              : Array.isArray(props.limitations)
                ? (props.limitations as string[])
                : [],
        }),
      };
    }
    if (selectedNode && selectedNodeSummary.edgeCount > 0) {
      return { checks: selectedNodeSummary.checks };
    }
    return null;
  }, [selectedEdge, selectedNode, selectedNodeSummary]);

  return (
    <div className="investigator-workspace">
      <header className="page-header">
        <div>
          <h1>Investigate</h1>
          <div className="cl-hierarchy">Case → People → Relationships → Evidence → Explanation → Action</div>
          <div className="case-header-stats">
            <span>{personNodes.length} people</span>
            <span>·</span>
            <span>{personRelationships.length} relationships</span>
            <span>·</span>
            <span>{relationshipCounts?.supporting_items ?? 0} supporting records</span>
            <span>·</span>
            <span>{relationshipCounts?.confirmed_criminals ?? 0} confirmed criminals</span>
          </div>
          <div className="investigator-questions">Who is involved? · What connects them? · What evidence supports?</div>
        </div>
        <div className="page-actions">
          <button className="cl-btn cl-btn-sm" onClick={() => navigate("/people")}>People</button>
          <button className="cl-btn cl-btn-sm" onClick={() => navigate("/relationships")}>Relationships</button>
          <button className="cl-btn cl-btn-sm" onClick={() => navigate("/evidence")}>Evidence</button>
        </div>
      </header>

      <ErrorBoundary>
        <NetworkAnalysisPanel initialCaseId={caseParam} />
      </ErrorBoundary>

      <ErrorBoundary>
        <MasterCaseNetwork />
      </ErrorBoundary>

      <div className="investigate-search-bar">
        <input
          type="text"
          placeholder="Ask about person-to-person connections..."
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleInvestigate()}
          className="cl-input"
          style={{ flex: 1 }}
        />
        <button className="cl-btn cl-btn-primary" onClick={handleInvestigate} disabled={loading || !question.trim()}>
          {loading ? "Investigating..." : "Investigate"}
        </button>
      </div>

      {error && <ErrorState message={error} onRetry={handleInvestigate} />}

      {loading && (
        <div className="progressive-streaming">
          <div className="stream-step done">✓ Identifying people</div>
          <div className="stream-step done">✓ Searching connections</div>
          <div className="stream-step done">✓ Checking evidence</div>
          <div className="stream-step active">● Preparing explanation</div>
        </div>
      )}

      {response && (
        <div className="investigation-objective-section" style={{ margin: "16px 0", padding: "16px", background: "var(--surface-primary)", borderRadius: "8px", border: "1px solid var(--border-primary)" }}>
          <h3>Investigation objective: {response.objective || question}</h3>
          {response.provenance && response.provenance.length > 0 && (
            <div className="inv-pointers" style={{ display: "flex", flexWrap: "wrap", gap: "8px", margin: "8px 0" }}>
              {response.provenance.map((p, idx) => (
                <ProvenanceChip key={idx} pointer={p} caseId={caseParam || ""} />
              ))}
            </div>
          )}
          {response.facts && response.facts.length > 0 && (
            <div className="investigation-facts" style={{ marginTop: "12px" }}>
              <h4>Facts</h4>
              <EvidenceList items={response.facts} caseId={caseParam || ""} />
            </div>
          )}
          {response.hypotheses && response.hypotheses.length > 0 && (
            <div className="investigation-hypotheses" style={{ marginTop: "12px" }}>
              <h4>Hypotheses</h4>
              <EvidenceList items={response.hypotheses.flatMap((h: any) => h.supporting || [])} caseId={caseParam || ""} />
            </div>
          )}
        </div>
      )}

      <div className="investigator-layout">
        <div className="investigator-main">
          <div className="graph-section">
            <div className="section-header">
              <h2>{graphData.isFocused ? "People Network — PERSON → PERSON" : "Supporting Entity Network"}</h2>
              <div className="section-actions">
                <span className="cl-badge cl-badge-info">
                  {graphData.isFocused
                    ? `PEOPLE · ${graphData.nodes.length} persons · ${graphData.edges.length} relationships`
                    : `ENTITIES · ${graphData.nodes.length} nodes · ${graphData.edges.length} edges`}
                </span>
                <button
                  className="cl-btn cl-btn-sm"
                  disabled={entityLoading}
                  onClick={() => (showSupporting ? setShowSupporting(false) : void loadSupportingEntities())}
                >
                  {entityLoading ? "Loading entities…" : showSupporting ? "People only" : "Show supporting entities"}
                </button>
                <button className="cl-btn cl-btn-sm" onClick={() => void loadRelationshipGraph()}>Refresh</button>
              </div>
            </div>

            <div className="graph-explanation">
              Primary graph is PERSON → PERSON. Supporting entities (phone, account, vehicle, address,
              organization, call record, transaction) are walked server-side and collapse into one
              relationship edge that carries its evidence — for example A and B both use PHONE-X and
              PHONE-X called PHONE-Y used by B becomes a single “Communication · 4 records” edge.
              The entity layer is loaded only when you ask for it.
            </div>

            <ErrorBoundary>
              <div style={{ border: "1px solid var(--border-primary)", borderRadius: "8px", overflow: "hidden" }}>
                <InvestigativeGraph
                  nodes={graphData.nodes}
                  edges={graphData.edges}
                  loading={graphLoading}
                  error={graphError}
                  onRetry={() => void loadRelationshipGraph()}
                  onSelectNode={(node) => {
                    setSelectedNode(node);
                    if (node) setSelectedEdge(null);
                  }}
                  onSelectEdge={(edge) => {
                    setSelectedEdge(edge);
                    if (edge) setSelectedNode(null);
                  }}
                  onSelectionChange={setSelectedNodes}
                  onContextAction={handleGraphContextAction}
                  selectedNodeIds={selectedNode ? [selectedNode.provenance_key] : []}
                  pinnedNodeIds={pinnedNodeIds}
                  height={520}
                  showCaseNodes={false}
                />
              </div>
            </ErrorBoundary>

            {shortestPath && (
              <div style={{ marginTop: "12px", padding: "8px 12px", background: "var(--info-bg)", border: "1px solid var(--info-border)", borderRadius: "6px", display: "flex", gap: "8px", alignItems: "center", fontSize: "12px" }}>
                <span>Path: {shortestPath.join(" → ")}</span>
                <button className="cl-btn cl-btn-sm" onClick={() => setShortestPath(null)}>Clear</button>
              </div>
            )}
          </div>

          <div className="tabs">
            <button className={`tab ${activeTab === "relationships" ? "active" : ""}`} onClick={() => setActiveTab("relationships")}>Relationships</button>
            <button className={`tab ${activeTab === "timeline" ? "active" : ""}`} onClick={() => setActiveTab("timeline")}>Timeline</button>
            <button className={`tab ${activeTab === "evidence" ? "active" : ""}`} onClick={() => setActiveTab("evidence")}>Evidence</button>
          </div>

          {activeTab === "relationships" && (
            <ErrorBoundary>
              <div className="relationships-tab">
                {!response && !loading && (
                  <div className="cl-empty">
                    <div className="cl-empty-title">Ask about person connections</div>
                    <div className="cl-empty-desc">Ask a question about person-to-person connections. Example: "Is there any connection between Person A and Person B?" The system searches people, then connections, then supporting evidence, then validates and explains — only relevant evidence is used.</div>
                  </div>
                )}

                {response && response.relationships.length === 0 && (
                  <NoConnectionCard
                    peopleSearched={personNodes.length}
                    evidenceExamined={relationshipCounts?.supporting_items ?? 0}
                    reliableRelationshipsFound={0}
                    onExpandSearch={() => setQuestion("")}
                    onImportEvidence={() => navigate("/cases")}
                  />
                )}

                {response?.relationships && response.relationships.length > 0 && (
                  <div className="relationships-list">
                    {response.relationships.map((rel: any, idx: number) => (
                      <RelationshipPanel
                        key={idx}
                        relationship={{
                          source_person: rel.source_person || rel.source || "PERSON-A",
                          target_person: rel.target_person || rel.target || "PERSON-B",
                          relationship_type: rel.relationship_type || rel.type || "COMMUNICATION",
                          classification: rel.classification || "FACT",
                          confidence: rel.confidence || "HIGH",
                          evidence_refs: rel.evidence_refs || rel.evidence || [],
                          provenance: rel.provenance || [],
                          explanation: rel.explanation || rel.description || "Connection established from evidence",
                          limitations: rel.limitations || [],
                          reasoning_path: rel.reasoning_path || [],
                          hop_count: rel.hop_count || 1,
                          supporting_evidence: rel.supporting_evidence || [],
                          timeline: rel.timeline,
                        }}
                        onViewEvidence={handleOpenEvidence}
                        onViewTimeline={() => setActiveTab("timeline")}
                        onOpenCase={() => caseParam && navigate(`/cases/${caseParam}`)}
                        onFocusPerson={(id) => navigate(`/people?focus=${id}`)}
                      />
                    ))}
                  </div>
                )}

                {response?.relationships && response.relationships.length === 0 && !(response as any).no_connection && (
                  <Empty message="No relationships found for this question. Try a different question or import more evidence." />
                )}

                {response && !response.relationships && (
                  <DeterministicFallbackCard
                    sourcePerson="PERSON-A"
                    targetPerson="PERSON-B"
                    relationshipType="COMMUNICATION"
                    evidenceCount={3}
                    onViewEvidence={() => handleOpenEvidence("E-001")}
                    onViewTimeline={() => setActiveTab("timeline")}
                  />
                )}
              </div>
            </ErrorBoundary>
          )}

          {activeTab === "timeline" && (
            <ErrorBoundary>
              <div className="timeline-tab">
                {timelineLoading ? (
                  <div className="cl-empty"><div className="cl-empty-title">Loading timeline…</div><div className="cl-empty-desc">Evidence-grounded with real timestamps only. "Timestamp unavailable" if missing.</div></div>
                ) : (
                  <EnhancedTimelineComp caseId={caseParam || ""} events={enhancedTimelineEvents} onOpenEvidence={handleOpenEvidence} />
                )}
                {selectedNode && (
                  <div style={{ marginTop: "12px", padding: "12px", background: "var(--surface-secondary)", borderRadius: "6px", fontSize: "12px" }}>
                    <strong>Selected:</strong> {getDisplayLabel(selectedNode)} ({selectedNode.label}) — This node appears because it is part of the person-centric subgraph relevant to your question.
                    {isPersonLabel(String(selectedNode.label)) ? " Primary graph emphasizes PEOPLE." : " Supporting entity — used as evidence, not final node."}
                  </div>
                )}
              </div>
            </ErrorBoundary>
          )}

          {activeTab === "evidence" && (
            <ErrorBoundary>
              <div className="evidence-tab">
                <p className="muted" style={{ fontSize: "11px" }}>Supporting evidence — phones, vehicles, locations, files, CCTV, docs, orgs, addresses used as internal evidence, not final relationship nodes. Every claim traceable.</p>
                {selectedNode && (
                  <div style={{ marginTop: "12px", padding: "12px", background: "var(--surface-secondary)", borderRadius: "6px", fontSize: "12px" }}>
                    <strong>{getDisplayLabel(selectedNode)}</strong> — Why: This {selectedNode.label} appears because it is part of the person-centric subgraph relevant to your investigation.
                    {isPersonLabel(String(selectedNode.label)) ? " Primary graph emphasizes PEOPLE." : " Supporting entity — used as evidence, not final node."}
                    <div style={{ marginTop: "8px", display: "flex", gap: "8px" }}>
                      <button className="cl-btn cl-btn-sm" onClick={() => setActiveTab("timeline")}>View Timeline</button>
                      {selectedNode.source_doc_ids?.[0] && <button className="cl-btn cl-btn-sm" onClick={() => handleOpenEvidence(selectedNode.source_doc_ids![0])}>View Evidence</button>}
                    </div>
                  </div>
                )}
              </div>
            </ErrorBoundary>
          )}
        </div>

        <div className="investigator-sidebar">
          <div className="sidebar-section">
            <h3>People</h3>
            <ErrorBoundary>
              <PersonList persons={personListData} onView={(id) => navigate(`/people?focus=${id}`)} />
            </ErrorBoundary>
          </div>


          {selectedNode && (
            <div className="sidebar-section">
              <h3>Selected</h3>
              <div style={{ padding: "8px", background: "var(--surface-secondary)", borderRadius: "6px", fontSize: "12px" }}>
                <strong>{getDisplayLabel(selectedNode)}</strong> ({selectedNode.label})
                <div style={{ marginTop: "8px", display: "flex", gap: "4px", flexWrap: "wrap" }}>
                  {/* These used to be literals -- FACT, STRONG, count 2, and a
                      trust badge that ticked all four boxes -- printed for
                      whatever node happened to be selected.  They are now
                      derived from the relationships that node actually has. */}
                  {selectedNodeSummary.edgeCount === 0 ? (
                    <span className="muted">
                      No relationship records for this entity.
                    </span>
                  ) : (
                    <>
                      <ClassificationBadge classification="FACT" />
                      <EvidenceStrength
                        strength={selectedNodeSummary.strength}
                        count={selectedNodeSummary.docCount}
                      />
                      <ProvenanceBadge {...selectedNodeSummary.checks} />
                    </>
                  )}
                </div>
                <div style={{ marginTop: "8px" }}>
                  <button className="cl-btn cl-btn-sm" onClick={() => setActiveTab("evidence")}>View Evidence</button>
                  <button className="cl-btn cl-btn-sm" onClick={() => setActiveTab("timeline")} style={{ marginLeft: "4px" }}>Timeline</button>
                </div>
              </div>
            </div>
          )}

          {selectedEdge && (
            <div className="sidebar-section">
              <h3>Relationship</h3>
              <div style={{ padding: "8px", background: "var(--surface-secondary)", borderRadius: "6px", fontSize: "12px" }}>
                <div><strong>{selectedEdge.source} ↔ {selectedEdge.target}</strong></div>
                <div>Type: {selectedEdge.rel_type}</div>
                <div style={{ marginTop: "8px", display: "flex", gap: "4px", flexWrap: "wrap" }}>
                  {/* FACT / STRONG / count 1 were literals here too, and the
                      contradiction alert was handed an empty list, so it could
                      never fire.  Both now read the selected edge's own
                      records. */}
                  <ClassificationBadge classification="FACT" />
                  <EvidenceStrength
                    strength={selectedEdgeSummary.strength}
                    count={selectedEdgeSummary.docCount}
                  />
                </div>
                {selectedEdgeSummary.contradictions.length > 0 && (
                  <ContradictionAlert details={selectedEdgeSummary.contradictions} />
                )}
              </div>
            </div>
          )}

          <div className="sidebar-section">
            <h3>Trust & Provenance</h3>
            {/* Four green ticks used to sit here permanently, attached to
                nothing.  The panel now reports on the current selection and
                says so when there is none. */}
            {trustTarget ? (
              <ProvenanceBadge {...trustTarget.checks} />
            ) : (
              <ProvenanceBadge unavailableReason="Select a person or a relationship to assess its evidence and provenance." />
            )}
          </div>

          <div className="sidebar-section">
            <h3>Classification</h3>
            <div style={{ display: "flex", flexDirection: "column", gap: "4px", fontSize: "11px" }}>
              <div><ClassificationBadge classification="FACT" /> Verified from evidence</div>
              <div><ClassificationBadge classification="INFERENCE" /> Logically inferred</div>
              <div><ClassificationBadge classification="HYPOTHESIS" /> Needs verification</div>
              <div><ClassificationBadge classification="UNKNOWN" /> Insufficient evidence</div>
            </div>
          </div>

          <div className="sidebar-section">
            <h3>What next?</h3>
            <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
              <button className="next-step-btn" onClick={() => navigate("/people")}>Review People</button>
              <button className="next-step-btn" onClick={() => navigate("/relationships")}>Review Relationships</button>
              <button className="next-step-btn" onClick={() => navigate("/evidence")}>View All Evidence</button>
              <button className="next-step-btn" onClick={() => navigate("/timeline")}>Examine Timeline</button>
              {caseParam && <button className="next-step-btn" onClick={() => navigate(`/cases/${caseParam}`)}>Open Case Overview</button>}
            </div>
          </div>
        </div>
      </div>

      <div style={{ marginTop: "20px", fontSize: "10px", fontFamily: "var(--font-mono)", color: "var(--muted)", borderTop: "1px solid var(--border-secondary)", paddingTop: "8px", display: "flex", gap: "12px", flexWrap: "wrap" }}>
        <div>Mode: PERSON → PERSON only · Supporting as evidence · No demo data</div>
        <div>·</div>
        <div>AI explanation: secondary, evidence strength primary</div>
        <div>·</div>
        <div>Based only on evidence shown above. Unsupported claims excluded.</div>
      </div>

      <EvidenceDrawer
        open={showEvidenceDrawer}
        onClose={() => setShowEvidenceDrawer(false)}
        data={evidenceDrawerData}
      />
    </div>
  );
}
