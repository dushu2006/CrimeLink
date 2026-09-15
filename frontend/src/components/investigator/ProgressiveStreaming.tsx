/**
 * Progressive Streaming UI — SEARCH→PEOPLE→CONNECTIONS→WHY?→EVIDENCE
 * Shows: ✓ Identifying people ✓ Searching graph ✓ Checking evidence ● Preparing explanation
 */

import { useEffect, useState } from "react";

interface Step {
  id: string;
  label: string;
  status: "pending" | "active" | "done" | "error";
  detail?: string;
}

interface Props {
  active: boolean;
  stage?: string;
  progress?: number;
  customSteps?: Step[];
}

export function ProgressiveStreaming({ active, stage, progress, customSteps }: Props) {
  const [steps, setSteps] = useState<Step[]>([
    { id: "identify", label: "Identifying people", status: "pending" },
    { id: "search", label: "Searching graph", status: "pending" },
    { id: "evidence", label: "Checking evidence", status: "pending" },
    { id: "explain", label: "Preparing explanation", status: "pending" },
  ]);

  useEffect(() => {
    if (!active) {
      setSteps((prev) => prev.map((s) => ({ ...s, status: "pending" })));
      return;
    }

    if (customSteps) {
      setSteps(customSteps);
      return;
    }

    // Map stage to steps
    const stageLower = (stage || "").toLowerCase();
    let currentStep = 0;

    if (stageLower.includes("identify") || stageLower.includes("person") || stageLower.includes("retriev")) {
      currentStep = 0;
    } else if (stageLower.includes("graph") || stageLower.includes("search") || stageLower.includes("traversal")) {
      currentStep = 1;
    } else if (stageLower.includes("evidence") || stageLower.includes("validat") || stageLower.includes("ranking")) {
      currentStep = 2;
    } else if (stageLower.includes("generat") || stageLower.includes("explain") || stageLower.includes("model")) {
      currentStep = 3;
    } else if (progress !== undefined) {
      if (progress < 25) currentStep = 0;
      else if (progress < 50) currentStep = 1;
      else if (progress < 75) currentStep = 2;
      else currentStep = 3;
    }

    setSteps((prev) =>
      prev.map((s, idx) => ({
        ...s,
        status: idx < currentStep ? "done" : idx === currentStep ? "active" : "pending",
      }))
    );
  }, [active, stage, progress, customSteps]);

  if (!active) return null;

  return (
    <div className="progressive-streaming">
      <div className="progressive-header">
        <span className="progressive-title">Investigation Progress</span>
        <span className="progressive-model">SEARCH → PEOPLE → CONNECTIONS → WHY? → EVIDENCE</span>
      </div>
      <div className="progressive-steps">
        {steps.map((step) => (
          <div key={step.id} className={`progressive-step progressive-step-${step.status}`}>
            <span className="step-icon">
              {step.status === "done" ? "✓" : step.status === "active" ? "●" : step.status === "error" ? "✗" : "○"}
            </span>
            <span className="step-label">{step.label}</span>
            {step.detail && <span className="step-detail">{step.detail}</span>}
            {step.status === "active" && <span className="step-spinner" aria-hidden />}
          </div>
        ))}
      </div>
      {progress !== undefined && (
        <div className="progressive-bar">
          <div className="progressive-bar-fill" style={{ width: `${Math.min(100, Math.max(0, progress))}%` }} />
        </div>
      )}
    </div>
  );
}

export default ProgressiveStreaming;
