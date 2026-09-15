/**
 * Trust & Provenance indicator — Signature UI element
 * ✓ Evidence verified, Source traceable, Provenance available, No unsupported claim
 */

interface Props {
  evidenceVerified?: boolean;
  sourceTraceable?: boolean;
  provenanceAvailable?: boolean;
  noUnsupported?: boolean;
}

export function ProvenanceBadge({ evidenceVerified = true, sourceTraceable = true, provenanceAvailable = true, noUnsupported = true }: Props) {
  return (
    <div className="provenance-badge">
      <div className="provenance-title">Why can I trust this finding? ⓘ</div>
      <div className="provenance-checks">
        <span className={`provenance-check ${evidenceVerified ? "verified" : "unverified"}`}>{evidenceVerified ? "✓" : "✗"} Evidence verified</span>
        <span className={`provenance-check ${sourceTraceable ? "verified" : "unverified"}`}>{sourceTraceable ? "✓" : "✗"} Source traceable</span>
        <span className={`provenance-check ${provenanceAvailable ? "verified" : "unverified"}`}>{provenanceAvailable ? "✓" : "✗"} Provenance available</span>
        <span className={`provenance-check ${noUnsupported ? "verified" : "unverified"}`}>{noUnsupported ? "✓" : "✗"} No unsupported claim detected</span>
      </div>
    </div>
  );
}

export default ProvenanceBadge;
