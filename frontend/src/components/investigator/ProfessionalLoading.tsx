/**
 * Professional Loading States — Priority 8 in UX redesign
 * 
 * Don't show: Spinner "Loading..."
 * Show: "Retrieving investigation context... Analyzing 1,284 entities... Computing network metrics..."
 */

interface ProfessionalLoadingProps {
  stage: string;
  message?: string;
  progress?: number;
  steps?: Array<{ label: string; status: "pending" | "running" | "done" | "failed"; time?: string }>;
  compact?: boolean;
}

const STAGE_MESSAGES: Record<string, { title: string; subtitle: string }> = {
  scope_ms: { title: "Retrieving investigation context", subtitle: "Loading active dataset and case scope" },
  resolution_ms: { title: "Resolving entities", subtitle: "Matching names to canonical records" },
  patterns_ms: { title: "Detecting unusual patterns", subtitle: "Running deterministic detectors over master network" },
  relationships_ms: { title: "Analyzing relationships", subtitle: "Discovering paths and connections" },
  hypotheses_ms: { title: "Evaluating hypotheses", subtitle: "Testing supporting and contradictory evidence" },
  gaps_ms: { title: "Identifying data gaps", subtitle: "Finding missing evidence and next steps" },
  narrative_ms: { title: "Generating assessment", subtitle: "Synthesizing findings (AI explains, graph is deterministic)" },
  PREPARING: { title: "Preparing dataset", subtitle: "Loading graph snapshot and evidence index" },
  ANALYZING_GRAPH: { title: "Analyzing graph structure", subtitle: "Computing degree, betweenness, PageRank, communities" },
  DETECTING_PATTERNS: { title: "Detecting patterns", subtitle: "Scanning for suspicious signals" },
  RETRIEVING_EVIDENCE: { title: "Retrieving evidence", subtitle: "Gathering supporting records and provenance" },
  SEARCHING_CONTRADICTIONS: { title: "Searching contradictions", subtitle: "Finding evidence that weakens findings" },
  REASONING: { title: "Reasoning over evidence", subtitle: "Big reasoning model — may take time, deterministic preserved" },
  VALIDATING: { title: "Validating evidence references", subtitle: "Checking canonical IDs and provenance" },
  GENERATING_EXPLANATION: { title: "Generating explanation", subtitle: "Producing investigator narrative" },
  QUEUED: { title: "Queued", subtitle: "Investigation queued — starting shortly" },
};

export function ProfessionalLoading({ stage, message, progress, steps, compact = false }: ProfessionalLoadingProps) {
  const stageInfo = STAGE_MESSAGES[stage] || {
    title: stage.replaceAll("_", " "),
    subtitle: message || "Processing...",
  };

  if (compact) {
    return (
      <div className="professional-loading-compact">
        <div className="loading-spinner-small" />
        <span className="loading-text-compact">{stageInfo.title}</span>
        {progress !== undefined && <span className="loading-progress-compact">{progress}%</span>}
      </div>
    );
  }

  return (
    <div className="professional-loading">
      <div className="professional-loading-header">
        <div className="loading-spinner" />
        <div className="loading-text">
          <span className="loading-title">{stageInfo.title}</span>
          <span className="loading-subtitle">{stageInfo.subtitle}</span>
          {message && message !== stageInfo.subtitle && (
            <span className="loading-message">{message}</span>
          )}
        </div>
        {progress !== undefined && (
          <div className="loading-progress">
            <span className="loading-progress-value">{progress}%</span>
          </div>
        )}
      </div>
      
      {progress !== undefined && (
        <div className="loading-progress-track">
          <div className="loading-progress-fill" style={{ width: `${progress}%` }} />
        </div>
      )}

      {steps && steps.length > 0 && (
        <div className="loading-steps">
          {steps.slice(-6).map((step, idx) => (
            <div key={idx} className={`loading-step loading-step-${step.status}`}>
              <span className={`loading-step-dot loading-step-dot-${step.status}`} />
              <span className="loading-step-label">{step.label}</span>
              {step.time && <span className="loading-step-time">{step.time}</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function ProfessionalLoadingSkeleton({ lines = 3 }: { lines?: number }) {
  return (
    <div className="professional-loading-skeleton">
      {Array.from({ length: lines }).map((_, i) => (
        <div key={i} className="skeleton-line" style={{ width: `${70 + Math.random() * 30}%` }} />
      ))}
    </div>
  );
}

export function RetrievalContextLoader({ query, entityCount, evidenceCount }: { query?: string; entityCount?: number; evidenceCount?: number }) {
  return (
    <div className="retrieval-context-loader">
      <div className="retrieval-loader-header">
        <div className="loading-spinner" />
        <span>Retrieving investigation context</span>
      </div>
      {query && <div className="retrieval-loader-query">"{query}"</div>}
      <div className="retrieval-loader-stats">
        {entityCount !== undefined && <span>{entityCount.toLocaleString()} entities</span>}
        {evidenceCount !== undefined && <span>· {evidenceCount.toLocaleString()} evidence</span>}
      </div>
      <div className="retrieval-loader-steps">
        <div className="retrieval-step active">
          <span className="retrieval-step-dot" /> Analyzing question intent
        </div>
        <div className="retrieval-step">
          <span className="retrieval-step-dot" /> Retrieving relevant subgraphs
        </div>
        <div className="retrieval-step">
          <span className="retrieval-step-dot" /> Ranking evidence by relevance
        </div>
      </div>
    </div>
  );
}
