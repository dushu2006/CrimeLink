/**
 * No Connection First-Class Result
 * UI: "No reliable person-to-person connection was established from the available evidence."
 * with People searched 12, Relevant evidence examined 31, Reliable relationships found 0
 */

interface Props {
  peopleSearched: number;
  evidenceExamined: number;
  reliableRelationshipsFound: number;
  reason?: string;
  searchedPersons?: string[];
  onExpandSearch?: () => void;
  onImportEvidence?: () => void;
}

export function NoConnectionCard({
  peopleSearched,
  evidenceExamined,
  reliableRelationshipsFound,
  reason,
  searchedPersons,
  onExpandSearch,
  onImportEvidence,
}: Props) {
  return (
    <div className="no-connection-card">
      <div className="no-connection-icon">🔍</div>
      <div className="no-connection-title">No Reliable Connection</div>
      <div className="no-connection-reason">
        {reason || "No reliable person-to-person connection was established from the available evidence."}
      </div>

      <div className="no-connection-stats">
        <div className="no-connection-stat">
          <span className="no-connection-stat-value">{peopleSearched}</span>
          <span className="no-connection-stat-label">People searched</span>
        </div>
        <div className="no-connection-stat">
          <span className="no-connection-stat-value">{evidenceExamined}</span>
          <span className="no-connection-stat-label">Relevant evidence examined</span>
        </div>
        <div className="no-connection-stat">
          <span className="no-connection-stat-value">{reliableRelationshipsFound}</span>
          <span className="no-connection-stat-label">Reliable relationships found</span>
        </div>
      </div>

      {searchedPersons && searchedPersons.length > 0 && (
        <div className="no-connection-searched">
          <span className="cl-label">Searched:</span> {searchedPersons.slice(0, 6).join(", ")}
          {searchedPersons.length > 6 && ` +${searchedPersons.length - 6} more`}
        </div>
      )}

      <div className="no-connection-actions">
        <button className="cl-btn cl-btn-sm cl-btn-secondary" onClick={onExpandSearch}>
          Expand Search
        </button>
        <button className="cl-btn cl-btn-sm" onClick={onImportEvidence}>
          Import More Evidence
        </button>
      </div>

      <div className="no-connection-note">
        Accuracy over quantity: prefer 3 highly supported connections over 30 weak/speculative. If insufficient, this result is preferable to incorrect.
      </div>
    </div>
  );
}

export default NoConnectionCard;
