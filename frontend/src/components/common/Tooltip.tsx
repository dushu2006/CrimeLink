import { useState, ReactNode } from "react";
interface Props { content: string; children: ReactNode; }
export function Tooltip({ content, children }: Props) {
  const [visible, setVisible] = useState(false);
  return (
    <span className="tooltip-wrapper" onMouseEnter={() => setVisible(true)} onMouseLeave={() => setVisible(false)} onFocus={() => setVisible(true)} onBlur={() => setVisible(false)} tabIndex={0} aria-describedby="tooltip">
      {children}
      {visible && <span className="tooltip" role="tooltip" id="tooltip">{content}</span>}
    </span>
  );
}
export default Tooltip;
