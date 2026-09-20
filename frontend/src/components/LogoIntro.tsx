/**
 * LogoIntro — Premium startup animation for the CrimeLink platform.
 *
 * Shows a minimal black-screen → logo-reveal → settle → fade-out sequence
 * when entering the authenticated application.  Pure CSS keyframes, no
 * external animation library required.
 *
 * Usage:
 *   <LogoIntro onComplete={() => setIntroDone(true)} />
 */

import { useEffect, useRef, useState } from "react";
import logoPng from "../logo/CrimeLink_Logo.png";

/* ------------------------------------------------------------------ */
/*  Timing (ms)                                                        */
/* ------------------------------------------------------------------ */
const REVEAL_DURATION = 500;   // Phase 2 — subtle reveal
const SETTLE_DURATION = 500;   // Phase 3 — settle
const ZOOM_OUT_DURATION = 500; // Phase 4 — full zoom passthrough into dashboard
const TOTAL = REVEAL_DURATION + SETTLE_DURATION + ZOOM_OUT_DURATION;

/* ------------------------------------------------------------------ */
/*  Inline styles (co-located so the component is fully self-contained) */
/* ------------------------------------------------------------------ */

const overlayBase: React.CSSProperties = {
  position: "fixed",
  inset: 0,
  zIndex: 99999,
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  background: "#000",
  willChange: "opacity",
  overflow: "hidden",
};

const logoBase: React.CSSProperties = {
  width: "clamp(130px, 20vw, 210px)",
  height: "auto",
  objectFit: "contain",
  willChange: "transform, opacity",
  userSelect: "none",
  pointerEvents: "none",
  transformOrigin: "center center",
};

/* ------------------------------------------------------------------ */
/*  Component                                                          */
/* ------------------------------------------------------------------ */

interface LogoIntroProps {
  /** Called once the full animation has completed. */
  onComplete: () => void;
}

export default function LogoIntro({ onComplete }: LogoIntroProps) {
  const [phase, setPhase] = useState<"reveal" | "settle" | "zoomin" | "done">("reveal");
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const calledRef = useRef(false);

  /* Detect reduced-motion preference once on mount. */
  const prefersReducedMotion =
    typeof window !== "undefined" &&
    window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

  useEffect(() => {
    /* Reduced motion: skip animation, short fade */
    if (prefersReducedMotion) {
      timerRef.current = setTimeout(() => {
        if (!calledRef.current) {
          calledRef.current = true;
          onComplete();
        }
      }, 200);
      return () => {
        if (timerRef.current) clearTimeout(timerRef.current);
      };
    }

    /* Phase 2 → 3: after reveal, move to settle. */
    timerRef.current = setTimeout(() => {
      setPhase("settle");

      /* Phase 3 → 4: after settle, zoom in forward into the screen */
      timerRef.current = setTimeout(() => {
        setPhase("zoomin");

        /* Phase 4 → done: complete transition and reveal app */
        timerRef.current = setTimeout(() => {
          setPhase("done");
          if (!calledRef.current) {
            calledRef.current = true;
            onComplete();
          }
        }, ZOOM_OUT_DURATION);
      }, SETTLE_DURATION);
    }, REVEAL_DURATION);

    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (phase === "done") return null;

  /* ---- Compute per-phase styles ---- */

  const overlayStyle: React.CSSProperties = {
    ...overlayBase,
    opacity: phase === "zoomin" ? 0 : 1,
    transition:
      phase === "zoomin"
        ? `opacity ${ZOOM_OUT_DURATION}ms cubic-bezier(0.4, 0, 0.2, 1)`
        : undefined,
  };

  const logoStyle: React.CSSProperties = {
    ...logoBase,
    ...(phase === "reveal"
      ? {
          animation: `crimelink-reveal-zoom ${REVEAL_DURATION}ms cubic-bezier(0.16, 1, 0.3, 1) forwards`,
        }
      : phase === "settle"
      ? {
          opacity: 1,
          transform: "scale(1)",
          transition: "transform 600ms ease-out",
        }
      : {
          /* Full zoom pass-through: zooms right past the camera / fills the screen as it reveals the app */
          opacity: 0,
          transform: "scale(5)",
          transition: `transform ${ZOOM_OUT_DURATION}ms cubic-bezier(0.4, 0, 0.2, 1), opacity ${ZOOM_OUT_DURATION * 0.85}ms cubic-bezier(0.4, 0, 0.2, 1)`,
        }),
  };

  return (
    <>
      <style>{`
        @keyframes crimelink-reveal-zoom {
          0% {
            opacity: 0;
            transform: scale(0.82);
          }
          100% {
            opacity: 1;
            transform: scale(1);
          }
        }
      `}</style>

      <div
        style={overlayStyle}
        aria-hidden="true"
        role="presentation"
      >
        <img
          src={logoPng}
          alt=""
          style={logoStyle}
          draggable={false}
        />
      </div>
    </>
  );
}
