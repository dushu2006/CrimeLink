/**
 * Trust & Provenance indicator — Signature UI element
 * ✓ Evidence verified, Source traceable, Provenance available, No unsupported claim
 *
 * Every check here is a claim about a specific finding, so every value is
 * required.  They used to default to `true`, and all three call sites passed
 * nothing, which rendered four green ticks on every finding regardless of what
 * supported it.  A check with no input is now rendered as unknown rather than
 * as a pass: defaulting a trust indicator to "trusted" is the one default this
 * component must never have.
 */

export interface ProvenanceCheckInput {
  evidenceVerified?: boolean;
  sourceTraceable?: boolean;
  provenanceAvailable?: boolean;
  noUnsupported?: boolean;
}

type Props = ProvenanceCheckInput & {
  /** Shown when the caller cannot assess a check at all. */
  unavailableReason?: string;
};

function Check({
  ok,
  label,
  unknownLabel,
}: {
  ok: boolean | undefined;
  label: string;
  unknownLabel: string;
}) {
  if (ok === undefined) {
    return (
      <span className="provenance-check unverified" title={unknownLabel}>
        ? {label}
      </span>
    );
  }
  return (
    <span className={`provenance-check ${ok ? "verified" : "unverified"}`}>
      {ok ? "✓" : "✗"} {label}
    </span>
  );
}

export function ProvenanceBadge({
  evidenceVerified,
  sourceTraceable,
  provenanceAvailable,
  noUnsupported,
  unavailableReason,
}: Props) {
  const allUnknown =
    evidenceVerified === undefined &&
    sourceTraceable === undefined &&
    provenanceAvailable === undefined &&
    noUnsupported === undefined;

  return (
    <div className="provenance-badge">
      <div className="provenance-title">Why can I trust this finding? ⓘ</div>
      {allUnknown && unavailableReason && (
        <div className="muted" style={{ fontSize: "11px", marginBottom: "4px" }}>
          {unavailableReason}
        </div>
      )}
      <div className="provenance-checks">
        <Check
          ok={evidenceVerified}
          label="Evidence verified"
          unknownLabel={unavailableReason ?? "Not assessed for this selection"}
        />
        <Check
          ok={sourceTraceable}
          label="Source traceable"
          unknownLabel={unavailableReason ?? "Not assessed for this selection"}
        />
        <Check
          ok={provenanceAvailable}
          label="Provenance available"
          unknownLabel={unavailableReason ?? "Not assessed for this selection"}
        />
        <Check
          ok={noUnsupported}
          label="No unsupported claim detected"
          unknownLabel={unavailableReason ?? "Not assessed for this selection"}
        />
      </div>
    </div>
  );
}

/**
 * Derive the four checks from one relationship's own records.
 *
 * Nothing here is asserted: a relationship is evidence-verified only if it
 * carries supporting records that name a real document, traceable only if a
 * provenance entry names one, and "no unsupported claim" only if the panel
 * actually recorded what the evidence does not establish.
 */
export function provenanceChecksFor(relationship: {
  supporting_evidence?: Array<{ timestamp?: string; source_doc_id?: string }>;
  evidence_refs?: string[];
  provenance?: Array<{ ref?: string; doc_id?: string }>;
  limitations?: string[];
}): ProvenanceCheckInput {
  const supporting = relationship.supporting_evidence ?? [];
  const docRefs = relationship.evidence_refs ?? [];
  const provenance = relationship.provenance ?? [];

  const namedDocs = provenance.filter(
    (entry) => (entry.ref ?? "").trim() !== "" || (entry.doc_id ?? "").trim() !== "",
  ).length;
  const directDocs = supporting.filter(
    (item) => (item.source_doc_id ?? "").trim() !== "",
  ).length;

  return {
    evidenceVerified: supporting.length > 0 && (directDocs > 0 || docRefs.length > 0),
    sourceTraceable: namedDocs > 0,
    provenanceAvailable: namedDocs > 0 || docRefs.length > 0,
    // "No unsupported claim" means the record states its own limits; a
    // relationship with no stated limitations has not established that.
    noUnsupported: (relationship.limitations ?? []).length > 0,
  };
}

export default ProvenanceBadge;
