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
        if ((data.activities || []).length === 0) {
          setError("No investigation activity found — database may need seeding");
        }
      } catch (e: any) {
        // No fake fallback — show Unavailable, not fake data
        setError(e.message || "Failed to load investigator activity — backend unavailable or MinIO/PostgreSQL not ready");
        setActivities([]);
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
        <div className="alert alert-warn" style={{ marginBottom: "12px", fontSize: "11px", fontFamily: "var(--font-mono)" }}>
          {error} — Status: Unavailable (no fake data shown). Check that seed_demo.py has run and MinIO/PostgreSQL are healthy.
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
