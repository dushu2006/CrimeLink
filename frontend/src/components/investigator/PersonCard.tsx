/**
 * Person Card — Center of CrimeLink
 * Person-centric, calm, evidence-first
 */

interface Props {
  id: string;
  pseudonym?: string;
  displayName?: string;
  relationshipsCount?: number;
  evidenceCount?: number;
  casesCount?: number;
  isPrimary?: boolean;
  onInvestigate?: () => void;
  onView?: () => void;
}

export function PersonCard({ id, pseudonym, displayName, relationshipsCount = 0, evidenceCount = 0, casesCount = 0, isPrimary, onInvestigate, onView }: Props) {
  return (
    <div className="person-card">
      <div className="person-card-header">
        <span className="person-card-id">{pseudonym || id}</span>
        {isPrimary && <span className="person-card-primary">Primary person</span>}
      </div>
      {displayName && <div className="person-card-name">{displayName}</div>}
      <div className="person-card-stats">
        <span>{relationshipsCount} relationships</span>
        <span>{evidenceCount} evidence records</span>
        <span>{casesCount} cases</span>
      </div>
      <div className="person-card-actions">
        <button className="cl-btn cl-btn-sm cl-btn-primary" onClick={onInvestigate}>Investigate</button>
        <button className="cl-btn cl-btn-sm" onClick={onView}>View</button>
      </div>
    </div>
  );
}

export default PersonCard;
