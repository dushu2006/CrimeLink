interface Props {
  role?: string;
  requiredRole?: string;
  message?: string;
  onGoBack?: () => void;
}

export function PermissionDenied({ role, requiredRole, message, onGoBack }: Props) {
  return (
    <div className="permission-denied" style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", padding: "48px 24px", textAlign: "center", background: "#ffffff", border: "1px solid #fee2e2", borderRadius: "8px" }}>
      <span className="material-symbols-outlined" style={{ fontSize: "48px", color: "#ef4444", marginBottom: "16px" }}>block</span>
      <h3 style={{ fontSize: "14px", fontWeight: 600, margin: "0 0 8px 0" }}>Permission denied</h3>
      <p style={{ fontSize: "12px", color: "#64748b", margin: "0 0 8px 0", maxWidth: "400px" }}>
        {message || `Your role (${role || "VIEWER"}) does not have permission to access this. Required: ${requiredRole || "INVESTIGATOR"}. Backend authorization enforced — Viewer cannot elevate via URL/localStorage/Zustand/API.`}
      </p>
      <div style={{ fontSize: "10px", fontFamily: "var(--font-mono)", color: "#94a3b8", marginBottom: "16px" }}>
        Security: No JWT bypass · No localStorage role trust · No disable auth · Viewer remains Viewer internally
      </div>
      {onGoBack && (
        <button className="cl-btn cl-btn-secondary" onClick={onGoBack}>Go back to cases</button>
      )}
    </div>
  );
}

export default PermissionDenied;
