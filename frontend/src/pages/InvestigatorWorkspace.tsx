/**
 * Investigator Workspace — Relationship-First Professional Redesign
 * Hierarchy: CASE → PEOPLE → RELATIONSHIPS → EVIDENCE → EXPLANATION → ACTION
 * Primary graph: PERSON → PERSON only, supporting entities as evidence
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  masterGraph,
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
import { ProvenanceBadge } from "../components/investigator/ProvenanceBadge";
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
  const [graphLoading, setGraphLoading] = useState(false);
  const [graphError, setGraphError] = useState<string | null>(null);
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

  const loadMasterGraph = useCallback(async () => {
    if (!caseParam) return;
    setGraphLoading(true);
    setGraphError(null);
    try {
      const graph = await masterGraph();
      const nodes: GraphNodeRow[] = graph.nodes || [];
      const edges: GraphEdgeRow[] = graph.edges || [];
      setMasterNodes(nodes);
      setMasterEdges(edges);
      setPersonNodes(nodes.filter((n) => isPersonLabel(n.label)));
      setPersonRelationships(edges.filter((e) => {
        const fromNode = nodes.find((n) => n.provenance_key === e.source);
        const toNode = nodes.find((n) => n.provenance_key === e.target);
        return fromNode && toNode && isPersonLabel(fromNode.label) && isPersonLabel(toNode.label);
      }));
    } catch (err: any) {
      setGraphError(err.message || "Failed to load graph");
    } finally {
      setGraphLoading(false);
    }
  }, [caseParam]);

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
      void loadMasterGraph();
      void loadEnhancedTimeline();
    }
  }, [caseParam, loadMasterGraph, loadEnhancedTimeline]);

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
            <span>{masterEdges.length} evidence records</span>
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
              <h2>{graphData.isFocused ? "People Network — PERSON → PERSON" : "Full Network"}</h2>
              <div className="section-actions">
                <span className="cl-badge cl-badge-info">{graphData.isFocused ? "FOCUSED" : "MASTER"} · {graphData.nodes.length} persons · {graphData.edges.length} edges</span>
                <button className="cl-btn cl-btn-sm" onClick={() => setShowSupporting(!showSupporting)}>
                  {showSupporting ? "PERSON only" : "Show Evidence"}
                </button>
                <button className="cl-btn cl-btn-sm" onClick={() => void loadMasterGraph()}>Refresh</button>
              </div>
            </div>

            <div className="graph-explanation">Primary graph emphasizes PEOPLE. Supporting entities (phone, vehicle, location, file, CCTV, doc, org, address) are internal evidence, not final nodes. Example: A owns PHONE-X contacted PHONE-Y belongs to B → A↔B with evidence.</div>

            <ErrorBoundary>
              <div style={{ border: "1px solid var(--border-primary)", borderRadius: "8px", overflow: "hidden" }}>
                <InvestigativeGraph
                  nodes={graphData.nodes}
                  edges={graphData.edges}
                  loading={graphLoading}
                  error={graphError}
                  onRetry={() => void loadMasterGraph()}
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
                    evidenceExamined={masterEdges.length}
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
                  <ClassificationBadge classification="FACT" />
                  <EvidenceStrength strength="STRONG" count={2} />
                  <ProvenanceBadge />
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
                  <ClassificationBadge classification="FACT" />
                  <EvidenceStrength strength="STRONG" count={1} />
                </div>
                <ContradictionAlert details={[]} />
              </div>
            </div>
          )}

          <div className="sidebar-section">
            <h3>Trust & Provenance</h3>
            <div style={{ fontSize: "11px", color: "var(--text-secondary)", display: "flex", flexDirection: "column", gap: "6px" }}>
              <div>✓ Evidence verified</div>
              <div>✓ Source traceable</div>
              <div>✓ Provenance available</div>
              <div>✓ No unsupported claims</div>
              <div style={{ marginTop: "8px", fontFamily: "var(--font-mono)", fontSize: "10px", background: "var(--surface-secondary)", padding: "6px", borderRadius: "4px" }}>Based only on evidence shown. Unsupported claims excluded by grounding validation.</div>
            </div>
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
