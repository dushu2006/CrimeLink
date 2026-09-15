interface Props {
  title?: string;
  message?: string;
  actionLabel?: string;
  onAction?: () => void;
  icon?: string;
}

export function EmptyState({ title = "No data", message = "No records found for this case. Add evidence or adjust filters.", actionLabel, onAction, icon = "inbox" }: Props) {
  return (
    <div className="empty-state" style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", padding: "48px 24px", textAlign: "center", background: "#ffffff", border: "1px solid #e2e8f0", borderRadius: "8px" }}>
      <span className="material-symbols-outlined" style={{ fontSize: "48px", color: "#94a3b8", marginBottom: "16px" }}>{icon}</span>
      <h3 style={{ fontSize: "14px", fontWeight: 600, margin: "0 0 8px 0" }}>{title}</h3>
      <p style={{ fontSize: "12px", color: "#64748b", margin: "0 0 16px 0", maxWidth: "400px" }}>{message}</p>
      {actionLabel && onAction && (
        <button className="cl-btn cl-btn-primary" onClick={onAction}>{actionLabel}</button>
      )}
      <div style={{ marginTop: "16px", fontSize: "10px", fontFamily: "var(--font-mono)", color: "#94a3b8" }}>
        Hosted demo should not show this for CR-1024 — if you see this on demo cases, seed may be incomplete. Run validation.
      </div>
    </div>
  );
}

export default EmptyState;
