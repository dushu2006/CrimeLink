import { useNavigate } from "react-router-dom";

type Action = { icon: string; title: string; description: string; to: string };

const DEFAULT_ACTIONS: Action[] = [
  { icon: "👥", title: "Review people", description: "Identify people connected to this investigation.", to: "/people" },
  { icon: "🔗", title: "Review relationships", description: "Understand how the people and records connect.", to: "/relationships" },
  { icon: "📄", title: "Review evidence", description: "Inspect the source records behind the findings.", to: "/evidence" },
  { icon: "🕒", title: "Examine timeline", description: "Reconstruct what happened and when.", to: "/timeline" },
];

export function WhatNext({ actions = DEFAULT_ACTIONS }: { actions?: Action[] }) {
  const navigate = useNavigate();
  return (
    <section className="what-next" aria-labelledby="what-next-title">
      <div className="what-next-heading">
        <div>
          <p className="section-kicker">Continue the investigation</p>
          <h2 id="what-next-title">What next?</h2>
        </div>
        <span className="what-next-hint">Evidence → explanation → action</span>
      </div>
      <div className="what-next-grid">
        {actions.map((action) => (
          <button key={action.title} className="what-next-action" onClick={() => navigate(action.to)}>
            <span className="what-next-icon" aria-hidden="true">{action.icon}</span>
            <span className="what-next-copy"><strong>{action.title}</strong><span>{action.description}</span></span>
            <span className="what-next-arrow" aria-hidden="true">→</span>
          </button>
        ))}
      </div>
    </section>
  );
}
