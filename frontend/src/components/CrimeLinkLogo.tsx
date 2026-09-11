
export default function CrimeLinkLogo({
  className = "h-9 w-auto",
  showSubtitle = true,
}: {
  className?: string;
  showSubtitle?: boolean;
}) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 240 60"
      fill="none"
      className={className}
    >
      {/* Shield and Interconnected Network Nodes Symbol */}
      <g transform="translate(10, 6)">
        {/* Shield Outline in Law Enforcement Blue */}
        <path
          d="M24 4 L42 10 V24 C42 35 24 44 24 44 C24 44 6 35 6 24 V10 Z"
          stroke="#1D4ED8"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          fill="#EFF6FF"
        />
        {/* Network Links */}
        <line x1="24" y1="14" x2="15" y2="22" stroke="#2563EB" strokeWidth="1.5" />
        <line x1="24" y1="14" x2="33" y2="22" stroke="#2563EB" strokeWidth="1.5" />
        <line x1="15" y1="22" x2="24" y2="34" stroke="#1D4ED8" strokeWidth="1.5" />
        <line x1="33" y1="22" x2="24" y2="34" stroke="#1D4ED8" strokeWidth="1.5" />
        <line
          x1="15"
          y1="22"
          x2="33"
          y2="22"
          stroke="#64748B"
          strokeWidth="1.2"
          strokeDasharray="2 2"
        />
        {/* Central Hub */}
        <circle cx="24" cy="23" r="4.5" fill="#DBEAFE" stroke="#1D4ED8" strokeWidth="1.5" />
        {/* Nodes */}
        <circle cx="24" cy="14" r="2.8" fill="#1D4ED8" />
        <circle cx="15" cy="22" r="3" fill="#2563EB" />
        <circle cx="33" cy="22" r="3" fill="#2563EB" />
        <circle cx="24" cy="34" r="3.2" fill="#1E40AF" />
        <circle cx="24" cy="23" r="1.8" fill="#1D4ED8" />
      </g>
      {/* Brand Wordmark & Subtitle in Deep Navy */}
      <text
        x="64"
        y="28"
        fontFamily="'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
        fontWeight="800"
        fontSize="20"
        fill="#0F172A"
        letterSpacing="1.2"
      >
        CRIMELINK
      </text>
      {showSubtitle && (
        <text
          x="64"
          y="43"
          fontFamily="'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
          fontWeight="600"
          fontSize="7.5"
          fill="#475569"
          letterSpacing="1.5"
        >
          INTELLIGENCE PLATFORM
        </text>
      )}
    </svg>
  );
}
