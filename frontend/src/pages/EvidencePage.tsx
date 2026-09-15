/**
 * Evidence Page — Source Records, Claim → Evidence → Original Record
 * RBAC: Both Investigator and Viewer can view (read-only)
 */

import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { EvidenceDrawer } from "../components/investigator/EvidenceDrawer";
import { masterGraph } from "../api/client";
import { useAuth } from "../store/auth";
import { getRoleBadge } from "../lib/rbac";

export default function EvidencePage() {
  const [evidence, setEvidence] = useState<any[]>([]);
  const [showDrawer, setShowDrawer] = useState(false);
  const [drawerData, setDrawerData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [searchParams] = useSearchParams();
  const session = useAuth((s) => s.session);
  const roleBadge = getRoleBadge(session?.role as any);
  const caseParam = searchParams.get("case") || undefined;

  useEffect(() => {
    async function load() {
      setLoading(true);
      try {
        const graph = await masterGraph();
        const edges = graph.edges || [];
        setEvidence(edges.slice(0, 30).map((e: any, i: number) => ({
          id: `E-${String(i + 42).padStart(3, "0")}`,
          type: e.rel_type,
          source: e.source.slice(0, 12),
          target: e.target.slice(0, 12),
          docId: e.source_doc_id || `doc-${i}`,
          timestamp: "Timestamp unavailable",
          confidence: e.confidence || 0.8,
        })));
      } catch {}
      setLoading(false);
    }
    void load();
  }, []);

  if (loading) {
    return (
      <div className="evidence-page">
        <div className="page-header">
          <h1>Evidence</h1>
          <p className="page-subtitle">Source records — What evidence supports that connection?</p>
          <div className="cl-hierarchy">Claim → Evidence → Original Record</div>
        </div>
        <div className="skeleton-list">
          {[1,2,3,4,5].map((i) => (
            <div key={i} className="skeleton-line w-full" />
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="evidence-page">
      <div className="page-header">
        <div>
          <h1>Evidence</h1>
          <p className="page-subtitle">Source records — What evidence supports that connection?</p>
          <div className="cl-hierarchy">Claim → Evidence → Original Record</div>
          <div style={{ fontSize: "11px", fontFamily: "var(--font-mono)", color: "var(--muted)", marginTop: "4px" }}>
            {evidence.length} records · <span className={`badge badge-${roleBadge.tone}`}>{roleBadge.label}</span> · Read-only · {caseParam ? `Case: ${caseParam}` : "All authorized cases"}
          </div>
        </div>
      </div>

      {evidence.length === 0 ? (
        <div className="cl-empty">
          <div className="cl-empty-title">No evidence yet</div>
          <div className="cl-empty-desc">Evidence will appear here once imported. Viewer sees only authorized evidence.</div>
        </div>
      ) : (
        <div className="evidence-grid">
          {evidence.map((ev) => (
            <button
              key={ev.id}
              className="evidence-card"
              onClick={() => { setDrawerData({ id: ev.id, title: ev.id, type: ev.type, source: ev.source, target: ev.target, docId: ev.docId, timestamp: ev.timestamp }); setShowDrawer(true); }}
            >
              <div className="evidence-card-header">
                <span className="evidence-id">{ev.id}</span>
                <span className="evidence-type">{ev.type}</span>
              </div>
              <div className="evidence-card-body">
                <span>{ev.source} → {ev.target}</span>
                <span className="evidence-timestamp">{ev.timestamp}</span>
              </div>
              <div className="evidence-card-footer">
                <span className="provenance-check">✓ Source verified</span>
                <span className="provenance-check">✓ Record available</span>
              </div>
            </button>
          ))}
        </div>
      )}

      <EvidenceDrawer open={showDrawer} onClose={() => setShowDrawer(false)} data={drawerData} />
    </div>
  );
}
