/**
 * Dataset import state must be the backend's, not the component's.
 *
 * The failure this pins down: Administration → Import Dataset → navigate to
 * Cases → come back, and the panel was empty. Refresh the browser mid-import
 * and the progress was gone. The import kept running on the server the whole
 * time — only the *view* of it lived in a React component.
 *
 * The console now hydrates from `GET /datasets/jobs/current` on mount, which
 * returns the authoritative job row (running or finished) plus a `running`
 * flag.  These tests pin that wiring in the real source: hydration happens on
 * mount, terminal state is rendered rather than discarded, `running` (not the
 * presence of a job) gates watching and uploading, and the watch is torn down
 * on unmount so leaving the page cancels nothing but leaks nothing.
 *
 * Run with: node --experimental-strip-types --test tests/
 */

import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const read = (rel) => readFileSync(join(ROOT, rel), "utf8");

const CONSOLE = read("src/components/DatasetConsole.tsx");
const CLIENT = read("src/api/client.ts");

// ---------------------------------------------------------------------------
// The API client exposes the authoritative endpoint
// ---------------------------------------------------------------------------

test("the client hydrates from the persisted job endpoint", () => {
  assert.ok(
    CONSOLE.includes("getCurrentDatasetJob"),
    "the console asks the backend what the current job is",
  );
  assert.ok(
    CLIENT.includes('api("/datasets/jobs/current")'),
    "hydration reads the dataset job row, not local state",
  );
  assert.ok(
    /getCurrentDatasetJob\(\):\s*Promise<\{ job: DatasetJob \| null; running: boolean \}>/.test(
      CLIENT,
    ),
    "the running flag is part of the contract",
  );
});

test("no import progress is reconstructed from frontend state", () => {
  // Progress, stage and status may only come from the job row. A timer or a
  // Date.now() derived percentage would be a simulated import.
  assert.ok(!CONSOLE.includes("setInterval"), "no client-side progress ticker");
  assert.ok(!CONSOLE.includes("Date.now()"), "progress is not derived from clock deltas");
  assert.ok(
    !/localStorage|sessionStorage/.test(CONSOLE),
    "browser storage is not treated as authoritative state",
  );
});

// ---------------------------------------------------------------------------
// Hydration on mount, and on remount after navigation or refresh
// ---------------------------------------------------------------------------

test("hydration runs on mount and is cancelled cleanly", () => {
  const hydration = CONSOLE.slice(
    CONSOLE.indexOf("// Re-attach to the job the database says"),
    CONSOLE.indexOf("async function upload()"),
  );
  assert.ok(hydration.length > 0, "the hydration effect exists");
  assert.ok(
    hydration.includes("let cancelled = false;") &&
      hydration.includes("if (cancelled || !current?.job) return;") &&
      /return \(\) => \{\s*cancelled = true;\s*\};/.test(hydration),
    "an in-flight hydration cannot setState after unmount",
  );
  assert.ok(
    hydration.includes("setJob(hydrated)"),
    "the job the backend reports becomes the rendered job",
  );
});

test("a finished job is shown, not discarded", () => {
  const hydration = CONSOLE.slice(
    CONSOLE.indexOf("// Re-attach to the job the database says"),
    CONSOLE.indexOf("async function upload()"),
  );
  // The old code bailed out here (`if (... current.job.terminal) return;`),
  // which is exactly what made a completed or failed import vanish.
  assert.ok(
    !/current\.job\.terminal\) return/.test(hydration),
    "terminal jobs are no longer dropped on hydration",
  );
  assert.ok(
    hydration.includes('if (!current.running || hydrated.terminal)'),
    "the running flag decides whether there is anything to watch",
  );
  assert.ok(
    hydration.includes('if (hydrated.status === "FAILED")'),
    "a failed import surfaces its real error after a refresh",
  );
  assert.ok(
    hydration.includes("hydrated.error"),
    "the failure shown is the backend's error text, not a generic one",
  );
});

test("history does not block the next import", () => {
  const hydration = CONSOLE.slice(
    CONSOLE.indexOf("// Re-attach to the job the database says"),
    CONSOLE.indexOf("async function upload()"),
  );
  assert.ok(
    hydration.includes("setBusy(false)"),
    "a finished job leaves the upload form usable",
  );
  // Only a live job blocks a new upload.
  assert.ok(
    CONSOLE.includes("if (job && !job.terminal) {"),
    "upload() refuses only while a job is genuinely running",
  );
});

// ---------------------------------------------------------------------------
// Leaving the page cancels the watch, never the job
// ---------------------------------------------------------------------------

test("the watch is torn down on unmount", () => {
  assert.ok(
    CONSOLE.includes("useEffect(() => () => unwatchRef.current?.(), []);"),
    "leaving Administration closes the socket instead of leaking one per visit",
  );
  assert.ok(
    CONSOLE.includes("unwatchRef.current?.();\n      setTransport(null);"),
    "re-attaching replaces the previous watch rather than stacking a second",
  );
  // Nothing here calls a cancel endpoint: the job belongs to the deployment,
  // so leaving the page must not stop it.
  assert.ok(
    !/jobs\/[^"'`]*cancel|cancelDatasetJob|\/cancel/.test(CONSOLE),
    "the console never cancels the job it leaves",
  );
  assert.ok(
    !CLIENT.includes("cancelDatasetJob"),
    "there is no client-side cancel for a dataset job",
  );
});

// ---------------------------------------------------------------------------
// The pipeline's own stages are the ones rendered
// ---------------------------------------------------------------------------

test("the stage list includes the verification gate", () => {
  const stages = CONSOLE.slice(CONSOLE.indexOf("const STAGES = ["), CONSOLE.indexOf("] as const;"));
  for (const stage of [
    "VALIDATING",
    "NORMALIZING",
    "INGESTING",
    "BUILDING_GRAPH",
    "INDEXING",
    "VERIFYING",
    "READY",
  ]) {
    assert.ok(stages.includes(`"${stage}"`), `${stage} is rendered`);
  }
});

test("a failed stage is marked where it failed, not at the end", () => {
  assert.ok(
    CONSOLE.includes("const failed = job.status === \"FAILED\";"),
    "failure comes from the job status",
  );
  assert.ok(
    /failed && index === reached\s*\?\s*"failed"/.test(CONSOLE),
    "the stage the backend recorded as reached is the one shown as failed",
  );
});
