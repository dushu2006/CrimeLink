interface Props {
  title?: string;
  message?: string;
  retryLabel?: string;
  onRetry?: () => void;
  type?: "backend" | "database" | "object" | "graph" | "session" | "file" | "investigation" | "unsupported" | "generic";
}

const MESSAGES: Record<string, { title: string; message: string; icon: string }> = {
  backend: { title: "Service temporarily unavailable", message: "Backend is restarting or unavailable. Your work is saved. Retry in a moment.", icon: "cloud_off" },
  database: { title: "Database unavailable", message: "PostgreSQL connection failed. System is retrying. Evidence metadata remains safe.", icon: "database" },
  object: { title: "File storage unavailable", message: "MinIO/S3 object storage is unavailable. Evidence files cannot be retrieved right now. Metadata still visible.", icon: "folder_off" },
  graph: { title: "Graph unavailable", message: "Neo4j graph is unavailable. People and relationships cannot be loaded. Case metadata still available.", icon: "hub" },
  session: { title: "Session expired", message: "Your session has expired. Please sign in again to continue investigation.", icon: "lock_clock" },
  file: { title: "File not found", message: "Requested file does not exist or object key does not resolve. Verify metadata/checksum/size. Object may have been moved.", icon: "draft" },
  investigation: { title: "No investigation activity", message: "No completed investigations for this case yet. Initiate investigation to generate findings.", icon: "search_off" },
  unsupported: { title: "Unsupported file type", message: "This file type cannot be previewed. Download to view locally.", icon: "description" },
  generic: { title: "Unavailable", message: "This resource is currently unavailable. Retry or contact administrator.", icon: "error" },
};

export function Unavailable({ title, message, retryLabel = "Retry", onRetry, type = "generic" }: Props) {
  const def = MESSAGES[type] || MESSAGES.generic;
  return (
    <div className="unavailable-state" style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", padding: "48px 24px", textAlign: "center", background: "#ffffff", border: "1px solid #e2e8f0", borderRadius: "8px" }}>
      <span className="material-symbols-outlined" style={{ fontSize: "48px", color: "#f59e0b", marginBottom: "16px" }}>{def.icon}</span>
      <h3 style={{ fontSize: "14px", fontWeight: 600, margin: "0 0 8px 0" }}>{title || def.title}</h3>
      <p style={{ fontSize: "12px", color: "#64748b", margin: "0 0 16px 0", maxWidth: "400px" }}>{message || def.message}</p>
      {onRetry && (
        <button className="cl-btn cl-btn-secondary" onClick={onRetry}>{retryLabel}</button>
      )}
      <div style={{ marginTop: "16px", fontSize: "10px", fontFamily: "var(--font-mono)", color: "#94a3b8" }}>
        No raw stack traces · No blank screens · Deterministic fallback · ErrorBoundary active
      </div>
    </div>
  );
}

export default Unavailable;
