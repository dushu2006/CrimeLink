import logoPng from "../logo/CrimeLink_Logo.png";

export default function CrimeLinkLogo({
  className = "sidebar-logo-img",
  showSubtitle = true,
  variant = "light",
}: {
  className?: string;
  showSubtitle?: boolean;
  variant?: "light" | "dark";
}) {
  const isLight = variant === "light";
  const wordmarkColor = isLight ? "#FFFFFF" : "#0F172A";
  const subtitleColor = isLight ? "#93C5FD" : "#475569";

  return (
    <div
      className={`crimelink-logo-container ${className}`}
      style={{ display: "inline-flex", alignItems: "center", gap: "10px" }}
    >
      <img
        src={logoPng}
        alt="CrimeLink Logo"
        style={{
          height: "36px",
          width: "36px",
          objectFit: "contain",
          borderRadius: "8px",
          flexShrink: 0,
        }}
      />
      {showSubtitle && (
        <div style={{ display: "flex", flexDirection: "column", lineHeight: 1.1 }}>
          <span
            style={{
              fontFamily: "var(--cl-font-display, Inter, sans-serif)",
              fontWeight: 800,
              fontSize: "16px",
              letterSpacing: "0.08em",
              color: wordmarkColor,
            }}
          >
            CRIMELINK
          </span>
          <span
            style={{
              fontFamily: "var(--cl-font-mono, monospace)",
              fontSize: "8.5px",
              fontWeight: 700,
              letterSpacing: "0.14em",
              color: subtitleColor,
              marginTop: "2px",
              textTransform: "uppercase",
            }}
          >
            Intelligence Platform
          </span>
        </div>
      )}
    </div>
  );
}

