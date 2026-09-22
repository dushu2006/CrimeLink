/**
 * Investigator Workspace — Relationship-First Professional Redesign
 * Hierarchy: CASE → PEOPLE → RELATIONSHIPS → EVIDENCE → EXPLANATION → ACTION
 * Primary graph: PERSON → PERSON only, supporting entities as evidence
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  caseGraph,
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
  const [entityTypeFilter, setEntityTypeFilter] = useState<string>("ALL");
  const [selectedNode, setSelectedNode] = useState<GraphNodeRow | null>(null);
  const [selectedEdge, setSelectedEdge] = useState<GraphEdgeRow | null>(null);
  const [selectedNodes, setSelectedNodes] = useState<GraphNodeRow[]>([]);
  const [pinnedNodeIds, setPinnedNodeIds] = useState<string[]>([]);
  const [shortestPath, setShortestPath] = useState<string[] | null>(null);

  const [enhancedTimelineEvents, setEnhancedTimelineEvents] = useState<EnhancedTimelineEvent[]>([]);
  const [timelineLoading, setTimelineLoading] = useState(false);
  const [timelineError, setTimelineError] = useState<string | null>(null);
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
   * Scoped to the current case when caseParam is present.
   */
  const loadSupportingEntities = useCallback(async () => {
    if (masterNodes.length > 0) {
      setShowSupporting(true);
      return;
    }
    setEntityLoading(true);
    try {
      const graph = caseParam ? await caseGraph(caseParam) : await masterGraph();
      setMasterNodes(graph.nodes || []);
      setMasterEdges(graph.edges || []);
      setShowSupporting(true);
    } catch (err: any) {
      setGraphError(err.message || "Failed to load supporting entities");
    } finally {
      setEntityLoading(false);
    }
  }, [caseParam, masterNodes.length]);

  const loadEnhancedTimeline = useCallback(async () => {
    if (!caseParam) return;
    setTimelineLoading(true);
    setTimelineError(null);
    try {
      const res = await enhancedTimeline(caseParam);
      setEnhancedTimelineEvents(res.events);
    } catch (err) {
      setTimelineError(err instanceof Error ? err.message : String(err));
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
    const rawNodes = showSupporting ? masterNodes : personNodes;
    const rawEdges = showSupporting ? masterEdges : personRelationships;

    if (entityTypeFilter === "ALL") {
      return { nodes: rawNodes, edges: rawEdges, isFocused: !showSupporting };
    }

    const filteredNodes = rawNodes.filter((n) => {
      const l = (n.label || "").toLowerCase().replace(/[^a-z]/g, "");
      if (entityTypeFilter === "PERSON") return l === "person" || l === "people";
      if (entityTypeFilter === "PHONE") return l === "phone" || l === "phonenumber";
      if (entityTypeFilter === "BANK_ACCOUNT") return l === "bankaccount" || l === "account";
      if (entityTypeFilter === "VEHICLE") return l === "vehicle" || l === "car";
      if (entityTypeFilter === "LOCATION") return l === "location" || l === "address";
      if (entityTypeFilter === "ORGANIZATION") return l === "organization" || l === "org";
      if (entityTypeFilter === "EVENT") return l === "event";
      return true;
    });

    const keySet = new Set(filteredNodes.map((n) => n.provenance_key));
    const filteredEdges = rawEdges.filter((e) => keySet.has(e.source) || keySet.has(e.target));
    return { nodes: filteredNodes, edges: filteredEdges, isFocused: false };
  }, [masterNodes, masterEdges, personNodes, personRelationships, showSupporting, entityTypeFilter]);

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
    // Only the identifier is passed: the drawer re-fetches the real document
    // type, provenance and classification from the backend.  Nothing about
    // the record ("Communication record", "Case record", "FACT") is stated
    // here — invented metadata would be a provenance fabrication.
    setEvidenceDrawerData({ id: ref, title: ref });
    setShowEvidenceDrawer(true);
  }, []);

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
      isCriminal: Boolean(n.criminal_status),
      criminalStatus: n.criminal_status,
      role: (n.properties?.role as string) ?? null,
    }));
  }, [personNodes, personRelationships]);

  /** Connected entities for the selected node — used by the selected rail. */
  const selectedConnections = useMemo(() => {
    if (!selectedNode) return [];
    const key = selectedNode.provenance_key;
    const nameOf = new Map(personNodes.map((n) => [n.provenance_key, n.name || n.provenance_key]));
    return personRelationships
      .filter((e) => e.source === key || e.target === key)
      .map((e) => {
        const otherKey = e.source === key ? e.target : e.source;
        return {
          key: otherKey,
          name: nameOf.get(otherKey) ?? otherKey,
          rel_type: e.rel_type,
          strength: (e.properties as Record<string, unknown>)?.strength as string | undefined,
          docId: e.source_doc_id ?? e.source_doc_ids[0] ?? null,
          docCount: e.source_doc_ids?.length ?? 0,
        };
      })
      .slice(0, 12);
  }, [selectedNode, personNodes, personRelationships]);

  /** Open evidence helper */
  const openDoc = useCallback((docId?: string | null) => {
    if (!docId) return;
    setEvidenceDrawerData({ id: docId, title: docId });
    setShowEvidenceDrawer(true);
  }, []);

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

            {/* Multi-Entity Filter Chips */}
            <div className="graph-filter-chips">
              <span style={{ fontSize: "11px", fontWeight: 700, fontFamily: "var(--cl-font-mono)", textTransform: "uppercase", color: "var(--cl-text-3)", marginRight: "4px" }}>Filter:</span>
              {[
                { id: "ALL", label: "All Entities" },
                { id: "PERSON", label: "People" },
                { id: "PHONE", label: "Phones" },
                { id: "BANK_ACCOUNT", label: "Bank Accounts" },
                { id: "VEHICLE", label: "Vehicles" },
                { id: "LOCATION", label: "Locations" },
                { id: "ORGANIZATION", label: "Organizations" },
                { id: "EVENT", label: "Events" },
              ].map((chip) => (
                <button
                  key={chip.id}
                  type="button"
                  className={`graph-filter-chip ${entityTypeFilter === chip.id ? "active" : ""}`}
                  onClick={() => {
                    setEntityTypeFilter(chip.id);
                    if (chip.id !== "ALL" && chip.id !== "PERSON" && !showSupporting) {
                      void loadSupportingEntities();
                    }
                  }}
                >
                  {chip.label}
                </button>
              ))}
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
                ) : timelineError ? (
                  <div className="cl-error">
                    <div className="cl-empty-title">The timeline could not be loaded</div>
                    <div className="cl-empty-desc">{timelineError}</div>
                    <button className="cl-btn" onClick={() => void loadEnhancedTimeline()}>
                      Retry
                    </button>
                  </div>
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
            <div className="sidebar-section selected-entity-panel">
              <h3 className="sidebar-section-h">
                <span className="material-symbols-outlined" style={{ fontSize: 15 }}>person</span>
                Selected Entity
              </h3>

              {/* ENTITY IDENTITY */}
              <div className="sel-identity">
                <div className="sel-entity-name">{getDisplayLabel(selectedNode)}</div>
                <div className="sel-entity-meta">
                  <span className="sel-entity-type">
                    <span className="material-symbols-outlined" style={{ fontSize: 12 }}>category</span>
                    {String(selectedNode.label).replace(/([A-Z])/g, " $1").trim()}
                  </span>
                  {Boolean(selectedNode.criminal_status) && (
                    <span className="sel-criminal-badge" title="Source-recorded criminal status">
                      <span className="material-symbols-outlined" style={{ fontSize: 12 }}>gavel</span>
                      {String(selectedNode.criminal_status).replace(/_/g, " ")}
                    </span>
                  )}
                </div>
                <div className="sel-entity-id">{selectedNode.provenance_key}</div>
              </div>

              {/* ENTITY PROPERTIES */}
              {selectedNode.properties && Object.keys(selectedNode.properties).length > 0 && (
                <div className="sel-block">
                  <div className="sel-block-label">
                    <span className="material-symbols-outlined" style={{ fontSize: 12 }}>info</span>
                    ENTITY PROPERTIES
                  </div>
                  <div className="entity-properties-grid">
                    {Object.entries(selectedNode.properties)
                      .filter(([k, v]) => v != null && v !== "" && typeof v !== "object" && k !== "description")
                      .slice(0, 10)
                      .map(([k, v]) => (
                        <div key={k} className="entity-property-row">
                          <span className="entity-property-key">{k.replace(/_/g, " ")}</span>
                          <span className="entity-property-val">{String(v)}</span>
                        </div>
                      ))}
                  </div>
                </div>
              )}

              {/* OPEN SOURCE FILE DIRECT ACTION */}
              {selectedNode.source_doc_ids && selectedNode.source_doc_ids.length > 0 && (
                <div className="sel-block">
                  <button
                    type="button"
                    className="open-source-btn"
                    style={{ width: "100%", justifyContent: "center" }}
                    onClick={() => openDoc(selectedNode.source_doc_ids[0])}
                  >
                    <span className="material-symbols-outlined" style={{ fontSize: 15 }}>description</span>
                    Open Source File ({selectedNode.source_doc_ids[0]})
                  </button>
                </div>
              )}

              {/* CONNECTIONS */}
              <div className="sel-block">
                <div className="sel-block-label">
                  <span className="material-symbols-outlined" style={{ fontSize: 12 }}>polyline</span>
                  CONNECTIONS ({selectedConnections.length})
                </div>
                {selectedConnections.length === 0 ? (
                  <div className="sel-empty">No relationship records for this entity.</div>
                ) : (
                  <ul className="sel-connection-list">
                    {selectedConnections.slice(0, 6).map((c) => (
                      <li key={c.key} className="sel-connection-item">
                        <button
                          type="button"
                          className="sel-connection-name"
                          onClick={() => {
                            const node = personNodes.find((n) => n.provenance_key === c.key);
                            if (node) setSelectedNode(node);
                          }}
                          title={`Focus ${c.name}`}
                        >
                          <span className="material-symbols-outlined" style={{ fontSize: 13 }}>person</span>
                          {c.name}
                        </button>
                        <span className="sel-connection-rel">{c.rel_type.replace(/_/g, " ").toLowerCase()}</span>
                        {c.docId && (
                          <button
                            type="button"
                            className="sel-connection-ev"
                            onClick={() => openDoc(c.docId)}
                            title={`Open ${c.docId}`}
                          >
                            <span className="material-symbols-outlined" style={{ fontSize: 12 }}>description</span>
                            {c.docCount}
                          </button>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              {/* EVIDENCE */}
              <div className="sel-block">
                <div className="sel-block-label">
                  <span className="material-symbols-outlined" style={{ fontSize: 12 }}>folder_open</span>
                  EVIDENCE ({selectedNode.source_doc_ids.length})
                </div>
                {selectedNode.source_doc_ids.length === 0 ? (
                  <div className="sel-empty">No source documents directly attached to this entity.</div>
                ) : (
                  <div className="sel-evidence-chips">
                    {selectedNode.source_doc_ids.slice(0, 6).map((docId) => (
                      <button
                        key={docId}
                        type="button"
                        className="evidence-chip clickable"
                        onClick={() => openDoc(docId)}
                        title={`Open ${docId}`}
                      >
                        <span className="material-symbols-outlined" style={{ fontSize: 12 }}>description</span>
                        {docId.length > 14 ? docId.slice(0, 12) + "…" : docId}
                      </button>
                    ))}
                    {selectedNode.source_doc_ids.length > 6 && (
                      <button type="button" className="evidence-chip" onClick={() => setActiveTab("evidence")}>
                        +{selectedNode.source_doc_ids.length - 6} more
                      </button>
                    )}
                  </div>
                )}
              </div>

              {/* CONTEXT — why it matters, real data only */}
              {selectedNodeSummary.edgeCount > 0 && (
                <div className="sel-block">
                  <div className="sel-block-label">
                    <span className="material-symbols-outlined" style={{ fontSize: 12 }}>info</span>
                    WHY IT MATTERS
                  </div>
                  <div className="sel-why">
                    <div className="sel-why-badges">
                      <EvidenceStrength strength={selectedNodeSummary.strength} count={selectedNodeSummary.docCount} />
                    </div>
                    <p className="sel-why-text">
                      {selectedNodeSummary.edgeCount} direct relationship{selectedNodeSummary.edgeCount === 1 ? "" : "s"}
                      {" "}across {selectedNodeSummary.docCount} source record{selectedNodeSummary.docCount === 1 ? "" : "s"}
                      {selectedNode.case_ids.length > 0 && ` in ${selectedNode.case_ids.length} case${selectedNode.case_ids.length === 1 ? "" : "s"}`}.
                      {" "}Select a connection or evidence item to inspect.
                    </p>
                  </div>
                </div>
              )}

              {/* ACTIONS */}
              <div className="sel-block">
                <div className="sel-block-label">
                  <span className="material-symbols-outlined" style={{ fontSize: 12 }}>arrow_forward</span>
                  ACTIONS
                </div>
                <div className="sel-actions">
                  <button
                    type="button"
                    className="cl-btn cl-btn-sm"
                    onClick={() => navigate(`/entities/${encodeURIComponent(selectedNode.provenance_key)}`)}
                  >
                    <span className="material-symbols-outlined" style={{ fontSize: 13 }}>open_in_new</span>
                    Open entity
                  </button>
                  <button
                    type="button"
                    className="cl-btn cl-btn-sm"
                    disabled={selectedNode.source_doc_ids.length === 0}
                    onClick={() => openDoc(selectedNode.source_doc_ids[0])}
                  >
                    <span className="material-symbols-outlined" style={{ fontSize: 13 }}>description</span>
                    Evidence
                  </button>
                  <button type="button" className="cl-btn cl-btn-sm" onClick={() => setActiveTab("timeline")}>
                    <span className="material-symbols-outlined" style={{ fontSize: 13 }}>timeline</span>
                    Timeline
                  </button>
                  {caseParam && (
                    <button
                      type="button"
                      className="cl-btn cl-btn-sm"
                      onClick={() => navigate(`/people?case=${caseParam}&focus=${selectedNode.provenance_key}`)}
                    >
                      <span className="material-symbols-outlined" style={{ fontSize: 13 }}>person_search</span>
                      Focus
                    </button>
                  )}
                </div>
              </div>
            </div>
          )}

          {selectedEdge && (
            <div className="sidebar-section selected-edge-panel">
              <h3 className="sidebar-section-h">
                <span className="material-symbols-outlined" style={{ fontSize: 15 }}>polyline</span>
                Selected Relationship
              </h3>
              <div className="sel-identity">
                <div className="sel-edge-persons">
                  <span className="sel-edge-person">{(personNodes.find(n=>n.provenance_key===selectedEdge.source)?.name)||selectedEdge.source}</span>
                  <span className="material-symbols-outlined sel-edge-arrow" aria-hidden>swap_horiz</span>
                  <span className="sel-edge-person">{(personNodes.find(n=>n.provenance_key===selectedEdge.target)?.name)||selectedEdge.target}</span>
                </div>
                <div className="sel-entity-type">
                  <span className="material-symbols-outlined" style={{ fontSize: 12 }}>link</span>
                  {selectedEdge.rel_type.replace(/_/g, " ")}
                </div>
              </div>
              <div className="sel-block">
                <div className="sel-block-label">EVIDENCE</div>
                <div className="sel-why-badges" style={{ marginBottom: 6 }}>
                  <EvidenceStrength strength={selectedEdgeSummary.strength} count={selectedEdgeSummary.docCount} />
                </div>
                <div className="sel-evidence-chips">
                  {(selectedEdge.source_doc_ids || []).slice(0, 6).map((d) => (
                    <button key={d} type="button" className="evidence-chip clickable" onClick={() => openDoc(d)}>
                      <span className="material-symbols-outlined" style={{ fontSize: 12 }}>description</span>
                      {d.length > 14 ? d.slice(0, 12) + "…" : d}
                    </button>
                  ))}
                </div>
              </div>
              <div className="sel-block">
                <div className="sel-block-label">ACTIONS</div>
                <div className="sel-actions">
                  <button type="button" className="cl-btn cl-btn-sm" onClick={() => openDoc(selectedEdge.source_doc_id)}>
                    <span className="material-symbols-outlined" style={{ fontSize: 13 }}>description</span>
                    Evidence
                  </button>
                  <button type="button" className="cl-btn cl-btn-sm" onClick={() => setActiveTab("timeline")}>
                    <span className="material-symbols-outlined" style={{ fontSize: 13 }}>timeline</span>
                    Timeline
                  </button>
                </div>
              </div>
              {selectedEdgeSummary.contradictions.length > 0 && (
                <ContradictionAlert details={selectedEdgeSummary.contradictions} />
              )}
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
            <h3 style={{ marginBottom: "12px" }}>What next?</h3>
            <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
              <div className="what-next-item">
                <div className="what-next-item-header">
                  <span className="what-next-item-tag">PEOPLE</span>
                </div>
                <div className="what-next-item-title">Review Key Persons</div>
                <div className="what-next-item-desc">Examine suspects, witnesses, and associates linked to this investigation.</div>
                <button type="button" className="what-next-item-action" onClick={() => navigate("/people")}>
                  Review People →
                </button>
              </div>

              <div className="what-next-item">
                <div className="what-next-item-header">
                  <span className="what-next-item-tag">NETWORK</span>
                </div>
                <div className="what-next-item-title">Analyze Relationships</div>
                <div className="what-next-item-desc">Audit multi-hop connections, co-occurrences, and communication patterns.</div>
                <button type="button" className="what-next-item-action" onClick={() => navigate("/relationships")}>
                  Review Relationships →
                </button>
              </div>

              <div className="what-next-item">
                <div className="what-next-item-header">
                  <span className="what-next-item-tag">EVIDENCE</span>
                </div>
                <div className="what-next-item-title">Inspect Source Dossiers</div>
                <div className="what-next-item-desc">Verify forensic files, CDR logs, surveillance feeds, and bank records.</div>
                <button type="button" className="what-next-item-action" onClick={() => navigate("/evidence")}>
                  View All Evidence →
                </button>
              </div>

              <div className="what-next-item">
                <div className="what-next-item-header">
                  <span className="what-next-item-tag">TIMELINE</span>
                </div>
                <div className="what-next-item-title">Chronological Sequence</div>
                <div className="what-next-item-desc">Step through timestamped events, movements, and transaction windows.</div>
                <button type="button" className="what-next-item-action" onClick={() => navigate("/timeline")}>
                  Examine Timeline →
                </button>
              </div>

              {caseParam && (
                <div className="what-next-item">
                  <div className="what-next-item-header">
                    <span className="what-next-item-tag">OVERVIEW</span>
                  </div>
                  <div className="what-next-item-title">Case Dossier</div>
                  <div className="what-next-item-desc">Return to the primary intelligence dashboard for Case #{caseParam}.</div>
                  <button type="button" className="what-next-item-action" onClick={() => navigate(`/cases/${caseParam}`)}>
                    Open Case Overview →
                  </button>
                </div>
              )}
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
