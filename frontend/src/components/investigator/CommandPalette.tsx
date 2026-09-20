/**
 * Command Palette — Ctrl+K
 * Professional desktop investigation app
 * Search cases, people, evidence...
 * Recent: Person A, Case CR-1024, Evidence E-042
 */

import { useEffect, useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";

interface Props {
  open: boolean;
  onClose: () => void;
}

interface CommandItem {
  id: string;
  label: string;
  type: "person" | "case" | "evidence" | "action" | "recent";
  description?: string;
  action?: () => void;
}

export function CommandPalette({ open, onClose }: Props) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<CommandItem[]>([]);
  const navigate = useNavigate();

  const recentItems: CommandItem[] = [
    { id: "recent-1", label: "Rajesh Kumar", type: "person", description: "12 relationships · 3 cases", action: () => navigate("/people?focus=P0001") },
    { id: "recent-2", label: "Case CR-2001", type: "case", description: "Active criminal conspiracy", action: () => navigate("/cases/CR-2001") },
    { id: "recent-3", label: "Evidence DOC-0001", type: "evidence", description: "First Information Report", action: () => navigate("/evidence") },
  ];

  const allCommands: CommandItem[] = [
    { id: "search", label: "Search cases, people, evidence...", type: "action", description: "Open full global search", action: () => navigate("/search") },
    { id: "people", label: "Go to People", type: "person", description: "Person-centric investigation", action: () => navigate("/people") },
    { id: "relationships", label: "Go to Relationships", type: "action", description: "Person → Person only evidence graph", action: () => navigate("/relationships") },
    { id: "evidence", label: "Go to Evidence", type: "evidence", description: "Source records & chain of custody", action: () => navigate("/evidence") },
    { id: "timeline", label: "Go to Timeline", type: "action", description: "Chronological event reconstruction", action: () => navigate("/timeline") },
    { id: "investigate", label: "Investigate Relationship", type: "action", description: "People → Relationships → Evidence → Action", action: () => navigate("/investigate") },
    { id: "cases", label: "Go to Cases", type: "case", description: "Investigation registry of all cases", action: () => navigate("/cases") },
  ];

  const handleSearch = useCallback((q: string) => {
    setQuery(q);
    if (!q.trim()) {
      setResults(recentItems);
      return;
    }
    const lower = q.toLowerCase();
    const filtered = allCommands.filter((c) => c.label.toLowerCase().includes(lower) || c.description?.toLowerCase().includes(lower));
    setResults(filtered.slice(0, 8));
  }, []);

  useEffect(() => {
    if (open) {
      handleSearch("");
    }
  }, [open, handleSearch]);

  if (!open) return null;

  return (
    <div className="command-palette-overlay" onClick={onClose} role="presentation">
      <div className="command-palette" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true" aria-label="Search and commands">
        <div className="command-palette-header">
          <span className="material-symbols-outlined command-search-icon">search</span>
          <input
            autoFocus
            type="text"
            className="command-palette-input"
            placeholder="Search CrimeLink... people, cases, evidence..."
            value={query}
            onChange={(e) => handleSearch(e.target.value)}
          />
          <kbd className="command-palette-kbd">ESC</kbd>
        </div>

        <div className="command-palette-body">
          {!query && (
            <div className="command-section">
              <div className="command-section-title">Recent Activity</div>
              {recentItems.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  className="command-item"
                  onClick={() => {
                    onClose();
                    if (item.action) item.action();
                    else navigate(`/${item.type === "recent" ? "people" : item.type}`);
                  }}
                >
                  <span className="command-item-icon">
                    <span className="material-symbols-outlined" style={{ fontSize: "18px" }}>
                      {item.type === "person" ? "person" : item.type === "case" ? "folder" : item.type === "evidence" ? "description" : "history"}
                    </span>
                  </span>
                  <div className="command-item-content">
                    <span className="command-item-label">{item.label}</span>
                    {item.description && <span className="command-item-desc">{item.description}</span>}
                  </div>
                  <span className="command-item-type">recent</span>
                </button>
              ))}
            </div>
          )}

          <div className="command-section">
            <div className="command-section-title">{query ? "Matching Results" : "Navigation & Commands"}</div>
            {results.map((item) => (
              <button
                key={item.id}
                type="button"
                className="command-item"
                onClick={() => {
                  onClose();
                  if (item.action) item.action();
                }}
              >
                <span className="command-item-icon">
                  <span className="material-symbols-outlined" style={{ fontSize: "18px" }}>
                    {item.type === "person" ? "person" : item.type === "case" ? "folder" : item.type === "evidence" ? "description" : "bolt"}
                  </span>
                </span>
                <div className="command-item-content">
                  <span className="command-item-label">{item.label}</span>
                  {item.description && <span className="command-item-desc">{item.description}</span>}
                </div>
                <span className="command-item-type">{item.type}</span>
              </button>
            ))}
            {results.length === 0 && query && (
              <div className="command-empty">
                <span>No results for "{query}"</span>
                <span className="command-empty-desc">Try searching by person name, case number, or document ID</span>
              </div>
            )}
          </div>

          <div className="command-section">
            <div className="command-section-title">Investigation Methodology</div>
            <div className="command-hint">People → Relationships → Evidence → Explanation → Action</div>
          </div>
        </div>

        <div className="command-palette-footer">
          <span>Navigate with ↑↓, select with Enter, close with ESC</span>
          <span>Case → People → Relationships → Evidence</span>
        </div>
      </div>
    </div>
  );
}

export default CommandPalette;
