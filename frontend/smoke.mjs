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

const API = process.env.CRIMELINK_API ?? "http://127.0.0.1:8000";
const BADGE = process.env.CRIMELINK_BADGE;
const PASSWORD = process.env.CRIMELINK_PASSWORD;
const root = new URL("./dist/", import.meta.url).pathname;
const html = fs.readFileSync(path.join(root, "index.html"), "utf8");
const asset = fs.readdirSync(path.join(root, "assets")).find((f) => f.endsWith(".js"));
const code = fs.readFileSync(path.join(root, "assets", asset), "utf8");

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

if (!login) {
  check("login or first-admin setup screen mounts", /Sign in|administrator|बैज|प्रशासक/i.test(text()));
} else {
  window.history.pushState({}, "", "/cases");
  window.dispatchEvent(new window.PopStateEvent("popstate"));
  await sleep(800);
  check("cases screen mounts", /Cases|प्रकरण|Register case|नया प्रकरण/.test(text()));
}

console.log("console errors:", errors.length ? errors.slice(0, 5) : "none");
if (errors.length) process.exitCode = 1;
