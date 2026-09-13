import { Link } from "react-router-dom";

export default function InvestigationPage() {
  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Deprecated — Use Master Investigation</h1>
          <p className="muted">
            Per-case Investigation Graph and AI Investigation have been removed. Investigation
            Analysis is now a global master workspace for the active dataset. Use the button below.
          </p>
        </div>
        <div className="row-actions">
          <Link className="btn btn-primary" to="/investigate">
            Go to Master Investigation
          </Link>
          <Link className="btn btn-secondary" to="/cases">
            Cases Registry
          </Link>
        </div>
      </header>
    </div>
  );
}
