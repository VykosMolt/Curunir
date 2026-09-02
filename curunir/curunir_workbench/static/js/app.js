// Shell: sign-in, navigation, hash router, display modes.
import { get, post, setToken, clearToken, token } from "./api.js";
import { setSession } from "./session.js";
import { h } from "./ui.js";
import * as v1 from "./views.js";
import * as v2 from "./views2.js";
import * as v3 from "./views3.js";
import * as pilot from "./pilot.js";

// Shown only when the server carries the V6.8 pilot routes.
const PILOT_NAV = ["PILOT", [["/pilot", "Pilot session"]]];
let pilotAvailable = false;

const NAV = [
  ["MISSION", [
    ["/overview", "Overview"], ["/investigation", "Investigation"],
    ["/search", "Search"], ["/activity", "Activity"]]],
  ["THE WORLD", [
    ["/claims", "Claims"],
    ["/entities", "Entities"], ["/events", "Events"], ["/timeline", "Timeline"],
    ["/graph", "Graph"], ["/map", "Map"], ["/evidence", "Evidence"],
    ["/sources", "Sources"]]],
  ["ANALYSIS", [
    ["/themes", "Themes"], ["/narratives", "Narratives"],
    ["/stakeholders", "Stakeholders"], ["/impact", "Impact"],
    ["/hypotheses", "Hypotheses"]]],
  ["FORECASTING", [
    ["/forecasts", "Forecasts"], ["/indicators", "Indicators"],
    ["/warnings", "Warnings"]]],
  ["OPERATIONS", [
    ["/collection", "Collection"], ["/watches", "Watches"], ["/tasks", "Tasks"],
    ["/review", "Review"]]],
  ["OUTPUT", [
    ["/annotations", "Annotations"], ["/reports", "Dossiers"]]],
];

// Route table: pattern -> handler(main, params, ...captures).
const ROUTES = [
  ["/pilot", pilot.pilotView],
  ["/overview", v1.overviewView],
  ["/investigation", v2.investigationView],
  ["/search", v1.searchView],
  ["/activity", v1.activityView],
  ["/entities/:id", v1.entityView], ["/entities", v1.entitiesView],
  ["/events/:id", v1.eventView], ["/events", v1.eventsView],
  ["/timeline", v1.timelineView],
  ["/graph", v1.graphView],
  ["/map", v1.mapViewPage],
  ["/evidence/:id", v1.evidenceView], ["/evidence", v1.evidenceListView],
  ["/sources/:id", v1.sourceView], ["/sources", v1.sourcesView],
  ["/claims/:id", v1.claimView], ["/claims", v1.claimsView],
  ["/themes/:id", v2.themeView], ["/themes", v2.themesView],
  ["/narratives/:id", v2.narrativeView], ["/narratives", v2.narrativesView],
  ["/stakeholders/:id", v2.stakeholderView], ["/stakeholders", v2.stakeholdersView],
  ["/impact/:id", v2.impactPathView], ["/impact", v2.impactView],
  ["/hypotheses/:id", v2.hypothesisView], ["/hypotheses", v2.hypothesesView],
  ["/forecasts/:id", v2.forecastView], ["/forecasts", v2.forecastsView],
  ["/indicators/:id", (m, p, id) => v1.recordView(m, p, "forecast_indicator", id)],
  ["/indicators", v2.indicatorsView],
  ["/warnings/:id", v2.warningView], ["/warnings", v2.warningsView],
  ["/collection", v2.collectionView],
  ["/watches", v2.watchesView],
  ["/tasks", v2.tasksView],
  ["/review", v2.reviewView],
  ["/annotations", v2.annotationsView],
  ["/reports/:id", v3.reportView], ["/reports", v3.reportsView],
  ["/record/:kind/:id", (m, p, kind, id) => v1.recordView(m, p, kind, id)],
];

function parseHash() {
  const raw = location.hash.replace(/^#/, "") || "/overview";
  const [path, query] = raw.split("?");
  return { path, params: new URLSearchParams(query || "") };
}

function matchRoute(path) {
  for (const [pattern, handler] of ROUTES) {
    const patternParts = pattern.split("/").filter(Boolean);
    const pathParts = path.split("/").filter(Boolean);
    if (patternParts.length !== pathParts.length) continue;
    const captures = [];
    let ok = true;
    for (let i = 0; i < patternParts.length; i++) {
      if (patternParts[i].startsWith(":")) captures.push(decodeURIComponent(pathParts[i]));
      else if (patternParts[i] !== pathParts[i]) { ok = false; break; }
    }
    if (ok) return { handler, captures };
  }
  return null;
}

async function route() {
  const main = document.getElementById("main");
  const { path, params } = parseHash();
  for (const a of document.querySelectorAll("#nav-links a[data-route]")) {
    a.classList.toggle("active", path === a.dataset.route
      || (a.dataset.route !== "/overview" && path.startsWith(a.dataset.route + "/")));
  }
  const match = matchRoute(path);
  if (!match) {
    main.replaceChildren(h("p", { class: "error" }, `unknown view: ${path}`));
    return;
  }
  await match.handler(main, params, ...match.captures);
  main.focus({ preventScroll: true });
}

function buildNav() {
  const host = document.getElementById("nav-links");
  host.replaceChildren();
  for (const [group, links] of pilotAvailable ? [PILOT_NAV, ...NAV] : NAV) {
    host.append(h("div", { class: "nav-group" }, group));
    for (const [routePath, label] of links) {
      host.append(h("a", { href: `#${routePath}`, dataset: { route: routePath } }, label));
    }
  }
}

// ---- display modes ----------------------------------------------------------
// "t" toggles the theme, "a" toggles auditor mode (raw identifiers), "/" focuses search.
const rootEl = document.documentElement;

function setTheme(value) {
  if (value) rootEl.setAttribute("data-theme", value);
  else rootEl.removeAttribute("data-theme");
  try {
    if (value) localStorage.setItem("curunir-theme", value);
    else localStorage.removeItem("curunir-theme");
  } catch {}
}

function toggleTheme() {
  const now = rootEl.getAttribute("data-theme");
  setTheme(now === "dark" ? "light" : now === "light" ? null : "dark");
}

function toggleAuditor() {
  const on = rootEl.getAttribute("data-auditor") === "on";
  rootEl.setAttribute("data-auditor", on ? "off" : "on");
  try { localStorage.setItem("curunir-auditor", on ? "off" : "on"); } catch {}
}

try {
  const saved = localStorage.getItem("curunir-theme");
  if (saved) setTheme(saved);
  if (localStorage.getItem("curunir-auditor") === "on") rootEl.setAttribute("data-auditor", "on");
} catch {}

addEventListener("keydown", (e) => {
  if (e.target.matches("input, textarea, select")) return;
  if (e.key === "/") {
    e.preventDefault();
    document.getElementById("quick-search").focus();
    return;
  }
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  if (e.key === "a") toggleAuditor();
  if (e.key === "t") toggleTheme();
});

window.addEventListener("hashchange", route);

// ---- boot -------------------------------------------------------------------

async function boot() {
  const login = document.getElementById("login");
  const shell = document.getElementById("shell");
  const form = document.getElementById("login-form");
  const errorEl = document.getElementById("login-error");

  async function tryStart() {
    if (!token()) return false;
    try {
      const session = await get("/api/session");
      setSession(session);
      // Feature-detect the pilot routes once, here: the plain workbench 404s.
      // Only a 404 means the routes are absent; any other failure is a pilot
      // server having a bad day, and the panel will show the error.
      pilotAvailable = await get("/v68/pilot/status").then(() => true, (err) => err.status !== 404);
      document.getElementById("actor-badge").textContent =
        `${session.actor_id} · ${session.roles.join("/")}`;
      login.classList.add("hidden");
      shell.classList.remove("hidden");
      buildNav();
      // On a pilot server the session panel is where work starts, so land
      // there rather than reading the mission before the session is open.
      if (pilotAvailable && !location.hash) {
        history.replaceState(null, "", "#/pilot");
      }
      await route();
      return true;
    } catch {
      clearToken();
      return false;
    }
  }

  const pilotLogin = document.getElementById("pilot-login");

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    setToken(document.getElementById("token").value.trim());
    errorEl.textContent = "";
    if (!pilotLogin.classList.contains("hidden")) {
      // On a pilot server the session opens before the first authenticated
      // read, so every recorded action, sign-in included, falls inside it. A
      // session already open for this role is resumed, not restarted.
      const role = document.getElementById("pilot-role").value;
      try {
        await post("/v68/pilot/start", { participant_role: role,
          note: document.getElementById("pilot-note").value });
      } catch (err) {
        if (err.status !== 409) {
          clearToken();
          errorEl.textContent = `session not started: ${err.message}`;
          return;
        }
      }
    }
    if (!(await tryStart())) errorEl.textContent = "unknown or disabled actor token";
  });

  document.getElementById("logout").addEventListener("click", () => {
    clearToken(); location.reload();
  });

  document.getElementById("quick-search").addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      location.hash = `#/search?q=${encodeURIComponent(e.target.value)}`;
    }
  });

  if (!(await tryStart())) {
    login.classList.remove("hidden");
    // A pilot server answers the unauthenticated probe with 401, the plain
    // workbench with 404; only then is the session block offered.
    fetch("/v68/pilot/status").then((r) => {
      if (r.status === 401) pilotLogin.classList.remove("hidden");
    }, () => {});
  }
}

boot();
