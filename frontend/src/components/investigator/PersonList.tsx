import { PersonCard } from "./PersonCard";

interface Person {
  id: string;
  pseudonym?: string;
  displayName?: string;
  relationshipsCount?: number;
  evidenceCount?: number;
  casesCount?: number;
}

interface Props {
  persons: Person[];
  onInvestigate?: (id: string) => void;
  onView?: (id: string) => void;
}

export function PersonList({ persons, onInvestigate, onView }: Props) {
  if (persons.length === 0) {
    return (
      <div className="cl-empty">
        <div className="cl-empty-title">No people identified yet</div>
        <div className="cl-empty-desc">Search for people to begin investigation. People are the center of CrimeLink.</div>
      </div>
    );
  }

  return (
    <div className="person-list">
      <div className="person-list-header">
        <h3>PEOPLE</h3>
        <span className="person-list-count">{persons.length} persons</span>
      </div>
      <div className="person-list-grid">
        {persons.map((p) => (
          <PersonCard
            key={p.id}
            id={p.id}
            pseudonym={p.pseudonym}
            displayName={p.displayName}
            relationshipsCount={p.relationshipsCount}
            evidenceCount={p.evidenceCount}
            casesCount={p.casesCount}
            onInvestigate={() => onInvestigate?.(p.id)}
            onView={() => onView?.(p.id)}
          />
        ))}
      </div>
    </div>
  );
}

export default PersonList;
