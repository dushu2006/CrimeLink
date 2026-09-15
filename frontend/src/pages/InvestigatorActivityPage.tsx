import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { InvestigatorActivity } from "../components/investigator/InvestigatorActivity";
import { EvidenceDrawer } from "../components/investigator/EvidenceDrawer";
import { api } from "../api/client";

export default function InvestigatorActivityPage() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const caseParam = searchParams.get("case") || undefined;
  const [showEvidence, setShowEvidence] = useState(false);
  const [evidenceData, setEvidenceData] = useState<any>(null);
  const [activities, setActivities] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function load() {
      setLoading(true);
      setError(null);
      try {
        const data = await api<{ activities: any[] }>("/investigator-activity");
        setActivities(data.activities || []);
      } catch (e: any) {
        setError(e.message);
        setActivities([
          {
            id: "INV-0042",
            investigator: "DEMO-INVESTIGATOR",
            investigatorName: "Demo Investigator",
            caseId: "case-001-demo",
            caseNumber: "CR-1024",
            subject: "PERSON-001 ↔ PERSON-002",
            finding: "Supported communication relationship",
            evidence: ["E-042", "E-103", "E-118"],
            evidenceStrength: "HIGH",
            classification: "FACT",
            completedAt: "15 Sep 2026",
            connectionPath: ["PERSON-001", "PERSON-002"],
            limitations: ["Purpose of association beyond documented records is unknown", "Criminal intent not established by this evidence alone"],
            provenance: "Verified from CDR and field reports",
          },
        ]);
      } finally {
        setLoading(false);
      }
    }
    load();
  }, []);

  if (loading) {
    return (
      <div className="page-skeleton">
        <div className="skeleton-hero">
          <div className="skeleton-line w-60" />
          <div className="skeleton-line w-40" />
        </div>
      </div>
    );
  }

  return (
    <div>
      {error && (
        <div className="alert alert-info" style={{ marginBottom: "12px", fontSize: "11px", fontFamily: "var(--font-mono)" }}>
          Backend unavailable ({error}) — showing cached demo activity. File retrieval requires MinIO availability.
        </div>
      )}
      <InvestigatorActivity
        activities={activities}
        onViewEvidence={(ref) => { setEvidenceData({ id: ref, title: ref, type: "Evidence", supports: "Investigation finding" }); setShowEvidence(true); }}
        onViewTimeline={() => navigate(`/timeline?case=${caseParam || ""}`)}
        onViewRelationship={(subject) => navigate(`/relationships?case=${caseParam || ""}`)}
      />
      <EvidenceDrawer open={showEvidence} onClose={() => setShowEvidence(false)} data={evidenceData} />
    </div>
  );
}
