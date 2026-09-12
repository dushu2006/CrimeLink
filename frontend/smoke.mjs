/**
 * Headless render smoke test for the CrimeLink console.
 *
 * Runs the *production bundle* in jsdom against the *real API*, so a broken
 * route, a renamed response field or a crash on first paint shows up here
 * rather than in front of an investigator.  Not part of `npm run build`; call
 * it explicitly (`node smoke.mjs`) against a running API.
 *
 * Optional: CRIMELINK_BADGE / CRIMELINK_PASSWORD to exercise authenticated
 * screens.  Without them, only the public login/setup screen is checked.
 */
import { JSDOM, VirtualConsole } from "jsdom";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const API = process.env.CRIMELINK_API ?? "http://127.0.0.1:8000";
const BADGE = process.env.CRIMELINK_BADGE;
const PASSWORD = process.env.CRIMELINK_PASSWORD;
const root = fileURLToPath(new URL("./dist/", import.meta.url));
const html = fs.readFileSync(path.join(root, "index.html"), "utf8");
const asset = fs.readdirSync(path.join(root, "assets")).find((f) => f.endsWith(".js"));
const code = fs.readFileSync(path.join(root, "assets", asset), "utf8");

const EXPECTED =
  /getContext|Could not create canvas|crimelink\.render_error|The above error occurred|setting 'font'/;
const errors = [];
const vc = new VirtualConsole();
vc.on("jsdomError", (e) => {
  if (!EXPECTED.test(e.message)) errors.push("jsdomError: " + e.message);
});
vc.on("error", (...a) => {
  const message = a.map(String).join(" ");
  if (!EXPECTED.test(message)) errors.push("console.error: " + message.slice(0, 200));
});

let login = null;
if (BADGE && PASSWORD) {
  login = await (
    await fetch(`${API}/api/v1/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ badge_number: BADGE, password: PASSWORD }),
    })
  ).json();
  if (!login.access_token) throw new Error("login failed: " + JSON.stringify(login));
}

const dom = new JSDOM(html.replace(/<script[^>]*><\/script>/g, ""), {
  runScripts: "outside-only",
  url: "http://localhost:5173/",
  pretendToBeVisual: true,
  virtualConsole: vc,
});
const { window } = dom;

{
  const store = new Map();
  Object.defineProperty(window, "localStorage", {
    value: {
      getItem: (k) => (store.has(k) ? store.get(k) : null),
      setItem: (k, v) => store.set(k, String(v)),
      removeItem: (k) => store.delete(k),
      clear: () => store.clear(),
    },
    configurable: true,
  });
  if (login) {
    store.set("crimelink.access", login.access_token);
    store.set("crimelink.refresh", login.refresh_token);
    store.set("crimelink.user", JSON.stringify(login));
  }
}

window.fetch = (input, init) => {
  const url = typeof input === "string" ? input : input.url;
  return fetch(url.startsWith("/") ? `${API}${url}` : url, init);
};
window.matchMedia =
  window.matchMedia || (() => ({ matches: false, addListener() {}, removeListener() {} }));

// jsdom has no canvas, and the focused evidence graph needs one: without this
// shim a master-scope answer throws inside the renderer and the error boundary
// replaces the page. The shim only makes 2d drawing a no-op; it asserts nothing.
const gradient = { addColorStop() {} };
const context2d = new Proxy(
  {
    canvas: { width: 0, height: 0 },
    measureText: () => ({ width: 10 }),
    getImageData: () => ({ data: new Uint8ClampedArray(4) }),
    createLinearGradient: () => gradient,
    createRadialGradient: () => gradient,
    createPattern: () => null,
    isPointInPath: () => false,
    getTransform: () => ({ a: 1, b: 0, c: 0, d: 1, e: 0, f: 0 }),
  },
  {
    get: (target, prop) => (prop in target ? target[prop] : () => undefined),
    set: (target, prop, value) => {
      target[prop] = value;
      return true;
    },
  }
);
window.HTMLCanvasElement.prototype.getContext = () => context2d;
window.WebSocket = class {
  constructor() {}
  close() {}
};

window.eval(code);

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const text = () => window.document.body.textContent ?? "";
/** Poll until `predicate` holds (the pipeline is slower cold than warm). */
async function waitFor(predicate, timeoutMs = 60000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (predicate()) return true;
    await sleep(500);
  }
  return false;
}
const answerRendered = () =>
  window.document.querySelectorAll("ul.inv-provenance-list > li").length > 0 ||
  /overall strength|Overall strength/i.test(text());
const check = (label, condition) => {
  console.log(`${condition ? "PASS" : "FAIL"}  ${label}`);
  if (!condition) process.exitCode = 1;
};

await sleep(400);

if (!login) {
  check("login or first-admin setup screen mounts", /Sign in|administrator|बैज|प्रशासक/i.test(text()));
} else {
  window.history.pushState({}, "", "/cases");
  window.dispatchEvent(new window.PopStateEvent("popstate"));
  await sleep(800);
  check("cases screen mounts", /Cases|प्रकरण|Register case|नया प्रकरण/.test(text()));

  // The investigator reasoning workspace: mount it, ask a real question
  // through the real form, and require a rendered answer. A page that only
  // paints is not evidence that the reasoning surface works.
  const headers = { Authorization: `Bearer ${login.access_token}` };
  const question = "Is there a connection between Harish Varma and Suresh Pradhan?";
  const cases = await (await fetch(`${API}/api/v1/cases?limit=8`, { headers })).json();
  const items = cases.items ?? [];

  // Pick a case whose answer really carries pointers (and no focused graph, which
  // needs a real canvas). Which case that is depends on the active dataset: the
  // harness asks the API rather than hard-coding a case id.
  let target = null;
  let targetAnswer = null;
  for (const candidate of items) {
    const res = await fetch(`${API}/api/v1/investigate`, {
      method: "POST",
      headers: { ...headers, "Content-Type": "application/json" },
      body: JSON.stringify({ question, case_id: candidate.id, max_patterns: 3 }),
    });
    if (!res.ok) continue;
    const answer = await res.json();
    if ((answer.provenance ?? []).length === 0) continue;
    const graphNodes = (answer.focused_graph?.nodes ?? []).length;
    if (!target || graphNodes === 0) {
      target = candidate;
      targetAnswer = answer;
      if (graphNodes === 0) break;
    }
  }

  if (!target) {
    check("a case exists whose answer carries provenance to open", false);
  } else {
    window.history.pushState({}, "", `/cases/${target.id}/investigate`);
    window.dispatchEvent(new window.PopStateEvent("popstate"));
    await sleep(1200);
    check(
      "investigator workspace mounts with its objective banner",
      /Investigation Analysis|जाँच विश्लेषण/.test(text()) &&
        /Investigation objective|जाँच का उद्देश्य/.test(text())
    );

    const input = window.document.querySelector('input[aria-label="Investigation question"]');
    const form = window.document.querySelector("form.inv-ask-form");
    if (!input || !form) {
      check("the ask form is present", false);
    } else {
      const setValue = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        "value"
      ).set;
      setValue.call(input, question);
      input.dispatchEvent(new window.Event("input", { bubbles: true }));
      await sleep(150);
      form.dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true }));
      const firstAnswer = await waitFor(answerRendered);
      check(
        "asking a question renders a reasoned answer",
        firstAnswer && /assessment|strength|evidence|hypothes/i.test(text())
      );
      check(
        "the answer states an honest strength instead of a bare percentage",
        /INSUFFICIENT|WEAK|MODERATE|STRONG/.test(text())
      );

      // Provenance path (§34.11 / §35.14). Every pointer the answer carries has to
      // render as what it is: a document or a source row opens, a graph edge, a
      // metric or a dataset-level record is shown as the reference it is. A
      // pointer that is not a document must never become a /documents/... link,
      // because that link dead-ends in the console.
      const provenanceRows = [...window.document.querySelectorAll("ul.inv-provenance-list > li")];
      const deadLinks = provenanceRows
        .map((row) => row.querySelector("a[href*='/documents/']"))
        .filter(Boolean)
        .map((anchor) => anchor.getAttribute("href") ?? "")
        .filter((href) => {
          const suffix = href.split("/documents/")[1] ?? "";
          return suffix.startsWith("dataset") || /^[0-9a-f]{64}$/.test(suffix);
        });
      const rendered = provenanceRows.filter((row) => row.querySelector("a, button, .inv-pointer"));
      const openable = (targetAnswer.provenance ?? []).filter(
        (pointer) => pointer.doc_id || pointer.origin_file
      );
      check(
        "the answer renders every provenance pointer it carries",
        provenanceRows.length === (targetAnswer.provenance ?? []).length
      );
      check(
        "no provenance pointer links to a non-document reference",
        rendered.length === provenanceRows.length && deadLinks.length === 0
      );
      check(
        "openable records and non-openable references render differently",
        openable.length === 0 ||
          provenanceRows.filter((row) => row.querySelector("a, button")).length >= openable.length
      );
    }

    // Master scope is where the answer's evidence carries every pointer kind at
    // once (documents, source rows, graph edges, metrics, dataset-level rows).
    window.history.pushState({}, "", "/investigate");
    window.dispatchEvent(new window.PopStateEvent("popstate"));
    await sleep(900);
    const masterInput = window.document.querySelector('input[aria-label="Investigation question"]');
    const masterForm = window.document.querySelector("form.inv-ask-form");
    if (!masterInput || !masterForm) {
      check("the master-scope ask form is present", false);
    } else {
      const setValue = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        "value"
      ).set;
      setValue.call(masterInput, question);
      masterInput.dispatchEvent(new window.Event("input", { bubbles: true }));
      await sleep(150);
      masterForm.dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true }));
      const masterAnswer = await waitFor(answerRendered);
      check(
        "the master-scope answer renders",
        masterAnswer && /Provenance/i.test(text())
      );

      const anchors = [...window.document.querySelectorAll("a[href*='/documents/']")];
      const deadLinks = anchors
        .map((anchor) => anchor.getAttribute("href") ?? "")
        .filter((href) => {
          const suffix = href.split("/documents/")[1] ?? "";
          return suffix.startsWith("dataset") || /^[0-9a-f]{64}$/.test(suffix);
        });
      const referenceChips = window.document.querySelectorAll(".inv-pointer-ref");
      // SMOKE_DEBUG=1 prints what rendered when a check fails.
      if (process.env.SMOKE_DEBUG) {
        console.log("debug master anchors:", anchors.length, "ref chips:", referenceChips.length,
          "dead:", deadLinks.length, "path:", window.location.pathname + window.location.search);
        console.log("debug master tail:", text().slice(-500).replace(/\s+/g, " "));
      }
      check(
        "non-document references render as references, never as document links",
        referenceChips.length > 0 && deadLinks.length === 0
      );

      // A link the console offers has to open: the first one is fetched back.
      const firstDocumentLink = anchors[0]?.getAttribute("href") ?? "";
      const documentId = firstDocumentLink.split("/documents/")[1];
      const opened = documentId
        ? await fetch(`${API}/api/v1/documents/${documentId}`, { headers })
        : null;
      check(
        "a document the answer links to opens from the API",
        Boolean(opened) && opened.status === 200
      );
    }
  }
}

console.log("console errors:", errors.length ? errors.slice(0, 5) : "none");
if (errors.length) process.exitCode = 1;

// The rendered app keeps timers and a (stubbed) socket alive; close the window
// and exit deterministically so a hung process never masquerades as a pass.
dom.window.close();
process.exit(process.exitCode ?? 0);
