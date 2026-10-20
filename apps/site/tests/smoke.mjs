/**
 * Site smoke tests: run against the BUILT site (astro preview) in a real
 * browser via the DevTools protocol — no test framework, no browser download
 * (runner images ship Google Chrome; locally any Chrome/Edge works).
 *
 * Exists because two CSS defects once shipped invisible: a dropped body rule
 * (invalid :global() selector) and silently merged declarations left surfaces
 * unpainted, which only showed up through force-dark theming. These checks
 * assert the paint, not just the markup.
 *
 * Usage: node tests/smoke.mjs [base-url]
 *   base-url defaults to http://localhost:4322/mtb-marathon-calendar/
 *   CHROME_PATH overrides the browser binary.
 */
import { spawn } from "node:child_process";
import path from "node:path";
import os from "node:os";
import { existsSync } from "node:fs";

const BASE = (process.argv[2] ?? "http://localhost:4322/mtb-marathon-calendar/").replace(/\/?$/, "/");
const PORT = 9229 + Math.floor(Math.random() * 40);

const CANDIDATES = [
  process.env.CHROME_PATH,
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "/usr/bin/google-chrome",
  "/usr/bin/google-chrome-stable",
  "/usr/bin/chromium-browser",
].filter(Boolean);

let failures = 0;
function check(name, condition, detail = "") {
  const status = condition ? "ok" : "FAIL";
  console.log(`[${status}] ${name}${detail ? ` — ${detail}` : ""}`);
  if (!condition) failures += 1;
}

async function withPage(run) {
  const binary = CANDIDATES.find((p) => existsSync(p));
  if (!binary) throw new Error(`no Chrome/Edge found (set CHROME_PATH); tried: ${CANDIDATES.join(", ")}`);

  const proc = spawn(binary, [
    `--remote-debugging-port=${PORT}`,
    "--headless=new",
    "--no-first-run",
    "--no-default-browser-check",
    `--user-data-dir=${path.join(os.tmpdir(), `mtb-smoke-${PORT}`)}`,
    "--window-size=1280,900",
    "about:blank",
  ], { stdio: "ignore" });

  try {
    let ws;
    for (let i = 0; i < 60 && !ws; i++) {
      await new Promise((r) => setTimeout(r, 250));
      try {
        const list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
        const page = list.find((t) => t.type === "page");
        if (!page) continue;
        ws = new WebSocket(page.webSocketDebuggerUrl);
        await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
      } catch {}
    }
    if (!ws) throw new Error("devtools never came up");

    let msgId = 0;
    const pending = new Map();
    ws.onmessage = (event) => {
      const msg = JSON.parse(event.data);
      if (msg.id && pending.has(msg.id)) {
        const { resolve, reject } = pending.get(msg.id);
        pending.delete(msg.id);
        msg.error ? reject(new Error(JSON.stringify(msg.error))) : resolve(msg.result);
      }
    };
    const send = (method, params = {}) => {
      const id = ++msgId;
      return new Promise((resolve, reject) => {
        pending.set(id, { resolve, reject });
        ws.send(JSON.stringify({ id, method, params }));
      });
    };
    const evaluate = async (expression) => {
      const r = await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
      return r.result.value;
    };
    const goto = async (url) => {
      await send("Page.enable");
      await send("Page.navigate", { url });
      // Wait for load + a beat for inline scripts.
      await new Promise((r) => setTimeout(r, 1200));
    };

    await run({ send, evaluate, goto });
    ws.close();
  } finally {
    proc.kill();
  }
}

await withPage(async ({ evaluate, goto }) => {
  // --- 1. Home page paints ---
  await goto(BASE);
  const theme = await evaluate("document.documentElement.dataset.theme");
  const bodyBg = await evaluate("getComputedStyle(document.body).backgroundColor");
  check("home: data-theme is set", theme === "light" || theme === "dark", `got ${theme}`);
  check("home: body is painted by the theme", /^(rgb|rgba)\(.*(0, 0, 0, 0\)|transparent)$/.test(bodyBg) === false, `body bg ${bodyBg}`);
  const cards = await evaluate("document.querySelectorAll('.event-card').length");
  check("home: event cards rendered", cards > 0, `${cards} cards`);
  const cardPainted = await evaluate(
    "getComputedStyle(document.querySelector('.event-card')).backgroundColor",
  );
  check("home: event card is painted", cardPainted !== "rgba(0, 0, 0, 0)", `card bg ${cardPainted}`);
  const canonical = await evaluate("document.querySelector('link[rel=canonical]')?.href");
  check("home: canonical present and absolute", /^https:\/\/[^/]+\/mtb-marathon-calendar\/$/.test(canonical ?? ""), canonical);
  const ogTitle = await evaluate("document.querySelector('meta[property=\"og:title\"]')?.content");
  check("home: og:title present", Boolean(ogTitle), ogTitle);

  // --- 2. Theme toggle persists across navigation ---
  await evaluate("document.getElementById('theme-toggle').click()");
  await new Promise((r) => setTimeout(r, 200));
  const saved = await evaluate("localStorage.getItem('mtb-theme')");
  check("toggle: choice saved to localStorage", saved === "light" || saved === "dark", saved);
  await goto(`${BASE}calendar`);
  const afterNav = await evaluate("document.documentElement.dataset.theme");
  check("toggle: theme survives navigation", afterNav === saved, `expected ${saved}, got ${afterNav}`);
  // Restore
  await evaluate("document.getElementById('theme-toggle').click()");

  // --- 3. Calendar page ---
  await goto(`${BASE}calendar`);
  const months = await evaluate("document.querySelectorAll('.month[data-month]').length");
  check("calendar: month sections rendered", months > 0, `${months} months`);

  // --- 4. A detail page with its calendar download ---
  await goto(BASE);
  const detailUrl = await evaluate(
    "document.querySelector('.event-card a[href*=\"/events/\"]')?.getAttribute('href')",
  );
  check("home: detail link is base-prefixed", (detailUrl ?? "").startsWith("/mtb-marathon-calendar/"), detailUrl);
  await goto(`${BASE}${detailUrl.replace(/^\/mtb-marathon-calendar\//, "")}`);
  const h1 = await evaluate("document.querySelector('h1')?.textContent?.trim()");
  check("detail: h1 rendered", Boolean(h1), h1);
  const icsHref = await evaluate("document.querySelector('a[download]')?.getAttribute('href')");
  check("detail: .ics download link present", Boolean(icsHref), icsHref);

  // --- 5. Infrastructure files ---
  for (const file of ["robots.txt", "sitemap-index.xml", "favicon.svg"]) {
    const res = await fetch(`${BASE}${file}`);
    check(`served: ${file}`, res.ok, `HTTP ${res.status}`);
  }
});

if (failures > 0) {
  console.error(`\n${failures} smoke check(s) failed`);
  process.exit(1);
}
console.log("\nall smoke checks passed");
