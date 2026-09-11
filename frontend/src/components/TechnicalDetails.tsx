import { useState, type ReactNode } from "react";

interface TechnicalDetailsProps {
  label?: string;
  children: ReactNode;
  defaultOpen?: boolean;
}

/**
 * Reusable collapsed-by-default disclosure component (WS 2.3 & 6.3).
 *
 * Keeps technical identifiers, timing metadata, and backend details accessible
 * without leaking into the primary reading path for judges and investigators.
 */
export function TechnicalDetails({
  label = "technical details",
  children,
  defaultOpen = false,
}: TechnicalDetailsProps) {
  const [open, setOpen] = useState(defaultOpen);

  return (
    <div className="technical-details">
      <button
        type="button"
        className="btn-tertiary technical-details-toggle"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
      >
        {open ? `Hide ${label}` : `Show ${label}`}
      </button>
      {open && <div className="technical-details-content">{children}</div>}
    </div>
  );
}
