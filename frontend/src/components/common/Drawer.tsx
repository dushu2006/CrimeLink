import { useEffect, ReactNode } from "react";
interface Props { open: boolean; onClose: () => void; title?: string; children: ReactNode; width?: string; }
export function Drawer({ open, onClose, title, children, width = "380px" }: Props) {
  useEffect(() => {
    const handleEsc = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    if (open) window.addEventListener("keydown", handleEsc);
    return () => window.removeEventListener("keydown", handleEsc);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="drawer-backdrop" onClick={onClose} role="presentation">
      <div className="drawer" style={{ width }} onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true" aria-label={title}>
        <header className="drawer-header">
          {title && <h2 className="drawer-title">{title}</h2>}
          <button className="drawer-close" onClick={onClose} aria-label="Close">×</button>
        </header>
        <div className="drawer-body">{children}</div>
      </div>
    </div>
  );
}
export default Drawer;
