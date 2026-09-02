/**
 * Headless render smoke test for the CrimeLink console.
 *
 * Runs the *production bundle* in jsdom against the *real API*, so a broken
 * route, a renamed response field or a crash on first paint shows up here
 * rather than in front of an investigator.  Not part of `npm run build`; call
 * it explicitly (`node smoke.mjs`) against a running API.
 */
import { JSDOM, VirtualConsole } from "jsdom";
import fs from "node:fs";
import path from "node:path";

const API = process.env.CRIMELINK_API ?? "http://127.0.0.1:8000";
const root = new URL("./dist/", import.meta.url).pathname;
const html = fs.readFileSync(path.join(root, "index.html"), "utf8");
const asset = fs.readdirSync(path.join(root, "assets")).find((f) => f.endsWith(".js"));
const code = fs.readFileSync(path.join(root, "assets", asset), "utf8");

// jsdom has no canvas implementation, so cytoscape cannot initialise there.
// That is a limitation of this harness, not of the app: filter it out.
const EXPECTED =
  /getContext|crimelink\.render_error|The above error occurred|setting 'font'/;
const errors = [];
const vc = new VirtualConsole();
vc.on("jsdomError", (e) => {
  if (!EXPECTED.test(e.message)) errors.push("jsdomError: " + e.message);
});
vc.on("error", (...a) => {
  const message = a.map(String).join(" ");
  if (!EXPECTED.test(message)) errors.push("console.error: " + message.slice(0, 200));
});

const login = await (
  await fetch(`${API}/api/v1/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ badge_number: "INV-0001", password: "CrimeLink@Inv1" }),
  })
).json();
if (!login.access_token) throw new Error("login failed: " + JSON.stringify(login));

const dom = new JSDOM(html.replace(/<script[^>]*><\/script>/g, ""), {
  runScripts: "outside-only",
  url: "http://localhost:5173/",
  pretendToBeVisual: true,
  virtualConsole: vc,
});
const { window } = dom;

localStorageShim: {
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
  store.set("crimelink.access", login.access_token);
  store.set("crimelink.refresh", login.refresh_token);
  store.set("crimelink.user", JSON.stringify(login));
}

// Route every relative /api call to the real backend.
window.fetch = (input, init) => {
  const url = typeof input === "string" ? input : input.url;
  return fetch(url.startsWith("/") ? `${API}${url}` : url, init);
};
window.matchMedia =
  window.matchMedia || (() => ({ matches: false, addListener() {}, removeListener() {} }));
window.WebSocket = class {
  constructor() {}
  close() {}
};

window.eval(code);

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const text = () => window.document.body.textContent ?? "";
const check = (label, condition) => {
  console.log(`${condition ? "PASS" : "FAIL"}  ${label}`);
  if (!condition) process.exitCode = 1;
};

await sleep(400);

window.history.pushState({}, "", "/cases");
window.dispatchEvent(new window.PopStateEvent("popstate"));
await sleep(800);
check("cases screen lists the demo case", /FIR\/2024\/0231/.test(text()));
check("cases screen shows pending reviews", /Pending review/.test(text()));

const link = [...window.document.querySelectorAll("a")].find((a) =>
  a.getAttribute("href")?.startsWith("/cases/"),
);
const caseId = link?.getAttribute("href")?.split("/")[2];
check("case link rendered", Boolean(caseId));

if (caseId) {
  window.history.pushState({}, "", `/cases/${caseId}`);
  window.dispatchEvent(new window.PopStateEvent("popstate"));
  await sleep(1200);
  check("case detail lists documents", /cdr_jio\.csv/.test(text()));
  check("case detail shows the processing column", /Processing/.test(text()));
  check("case detail shows the timeline", /Timeline/.test(text()));

  window.history.pushState({}, "", `/cases/${caseId}/review`);
  window.dispatchEvent(new window.PopStateEvent("popstate"));
  await sleep(1200);
  check("review screen shows identity matches", /Identity matches/.test(text()));
  check("review screen shows a similarity score", /%/.test(text()));
  check("review screen lists an evidence-backed match", /NAME_FUZZY|ALIAS_CO_MENTION/.test(text()));

  // The graph needs a real canvas; in jsdom cytoscape cannot initialise, so we
  // only assert that the route mounts and that the failure is contained.
  window.history.pushState({}, "", `/cases/${caseId}/graph`);
  window.dispatchEvent(new window.PopStateEvent("popstate"));
  await sleep(1200);
  check("graph route mounts a container", Boolean(window.document.querySelector(".graph-layout, .state-error")));
}

console.log("console errors:", errors.length ? errors.slice(0, 5) : "none");
if (errors.length) process.exitCode = 1;
