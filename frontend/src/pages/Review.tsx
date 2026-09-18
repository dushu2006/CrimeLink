import { useCallback, useEffect, useState } from "react";
import { useLocation, useParams, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import { t } from "../i18n";
import { Badge, Empty, ErrorState, Spinner } from "../components/Status";

interface NodeSide {
  provenance_key: string;
  name: string;
  label: string;
  confidence: number;
  aliases: string[];
  source_doc_ids: string[];
}

interface MatchItem {
  id: string;
  status: string;
  similarity_score: number;
  match_basis: string;
  evidence_doc_ids: string[];
  age_hours: number;
  sla_hours: number;
  sla_breached: boolean;
  resolution_note: string | null;
  source: NodeSide;
  target: NodeSide;
}

interface PatternItem {
  id: string;
  pattern_type: string;
  confidence: number;
  status: string;
  explanation: string;
  details: Record<string, unknown>;
  entities: { provenance_key: string; name: string; label: string }[];
  evidence_doc_ids: string[];
  detected_at: string | null;
}

const TABS = ["identity", "patterns"] as const;

export default function Review() {
  const { caseId = "" } = useParams();
  const location = useLocation();
  const [searchParams] = useSearchParams();
  const [tab, setTab] = useState<(typeof TABS)[number]>(
    searchParams.get("tab") === "patterns" || location.pathname === "/patterns"
      ? "patterns"
      : "identity",
  );
  const [matches, setMatches] = useState<MatchItem[] | null>(null);
  const [patterns, setPatterns] = useState<PatternItem[] | null>(null);
  const [sla, setSla] = useState<{ breached?: number; total?: number } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [busyId, setBusyId] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  // Fetch only the data the visible tab renders.  Loading both lists on every
  // mount made a single Review visit issue two requests (four in dev under
  // StrictMode) and helped exhaust the server rate limiter; the hidden tab's
  // list is fetched lazily when the investigator switches to it.
  const load = useCallback(() => {
    setError(null);
    if (tab === "identity") {
      api<{ items: MatchItem[]; sla: { breached?: number; total?: number } }>(
        `/resolution?case_id=${caseId}&limit=200`,
      )
        .then((matchData) => {
          setMatches(matchData.items);
          setSla(matchData.sla ?? null);
        })
        .catch((err: Error) => setError(err.message));
    } else {
      api<{ items: PatternItem[] }>(`/patterns?case_id=${caseId}&limit=200`)
        .then((patternData) => setPatterns(patternData.items))
        .catch((err: Error) => setError(err.message));
    }
  }, [caseId, tab]);

  useEffect(load, [load]);