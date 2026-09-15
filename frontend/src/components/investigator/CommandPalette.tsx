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
    { id: "recent-1", label: "Person A", type: "recent", description: "12 relationships · 3 cases" },
    { id: "recent-2", label: "Case CR-1024", type: "recent", description: "Active investigation" },
    { id: "recent-3", label: "Evidence E-042", type: "recent", description: "Communication record" },
  ];

  const allCommands: CommandItem[] = [
    { id: "search", label: "Search cases, people, evidence...", type: "action", description: "Global search", action: () => navigate("/search") },
    { id: "people", label: "Go to People", type: "action", description: "Person-centric investigation", action: () => navigate("/people") },
    { id: "relationships", label: "Go to Relationships", type: "action", description: "Person → Person only", action: () => navigate("/relationships") },
    { id: "evidence", label: "Go to Evidence", type: "action", description: "Source records", action: () => navigate("/evidence") },
    { id: "timeline", label: "Go to Timeline", type: "action", description: "Evidence-oriented", action: () => navigate("/timeline") },
    { id: "investigate", label: "Investigate Relationship", type: "action", description: "People → Relationships → Evidence → Explanation", action: () => navigate("/investigate") },
    { id: "cases", label: "Go to Cases", type: "action", description: "Investigation registry", action: () => navigate("/cases") },
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
    <div className="command-palette-overlay" onClick={onClose}>
      <div className="command-palette" onClick={(e) => e.stopPropagation()}>
        <div className="command-palette-header">
          <span className="material-symbols-outlined">search</span>
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
              <div className="command-section-title">Recent</div>
              {recentItems.map((item) => (
                <button key={item.id} className="command-item" onClick={() => { onClose(); navigate(`/${item.type === "recent" ? "people" : item.type}`); }}>
                  <span className="command-item-icon">{item.type === "recent" ? "🕒" : "📁"}</span>
                  <div className="command-item-content">
                    <span className="command-item-label">{item.label}</span>
                    <span className="command-item-desc">{item.description}</span>
                  </div>
                </button>
              ))}
            </div>
          )}

          <div className="command-section">
            <div className="command-section-title">{query ? "Results" : "Commands"}</div>
            {results.map((item) => (
              <button
                key={item.id}
                className="command-item"
                onClick={() => {
                  onClose();
                  if (item.action) item.action();
                }}
              >
                <span className="command-item-icon">
                  {item.type === "person" ? "👤" : item.type === "case" ? "📁" : item.type === "evidence" ? "📄" : "⚡"}
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
                <span className="command-empty-desc">Try searching people, cases, or evidence</span>
              </div>
            )}
          </div>

          <div className="command-section">
            <div className="command-section-title">Investigate</div>
            <div className="command-hint">People → Relationships → Evidence → Explanation → Action</div>
          </div>
        </div>

        <div className="command-palette-footer">
          <span>Navigate with ↑↓, select with Enter, close with ESC</span>
          <span>Case → People → Relationships → Evidence → Explanation → Action</span>
        </div>
      </div>
    </div>
  );
}

export default CommandPalette;
