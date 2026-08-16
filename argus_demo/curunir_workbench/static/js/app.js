// Shell: session, navigation, hash router.
import { get, setToken, clearToken, token } from "./api.js";
import { h } from "./ui.js";
import * as v1 from "./views.js";
import * as v2 from "./views2.js";
import * as v3 from "./views3.js";

const NAV = [
  ["MISSION", [
    ["/overview", "Overview"], ["/investigation", "Investigation"],
    ["/search", "Search"], ["/activity", "Activity"]]],
  ["WORLD", [
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

// route table: pattern → handler(main, params, ...captures)
const ROUTES = [
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
  ["/claims/:id", v1.claimView],
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
  for (const [group, links] of NAV) {
    host.append(h("div", { class: "nav-group" }, group));
    for (const [routePath, label] of links) {
      host.append(h("a", { href: `#${routePath}`, dataset: { route: routePath } }, label));
    }
  }
}

async function boot() {
  const login = document.getElementById("login");
  const shell = document.getElementById("shell");
  const form = document.getElementById("login-form");
  const errorEl = document.getElementById("login-error");

  async function tryStart() {
    if (!token()) return false;
    try {
      const session = await get("/api/session");
      document.getElementById("actor-badge").textContent =
        `${session.actor_id} · ${session.roles.join("/")}`;
      login.classList.add("hidden");
      shell.classList.remove("hidden");
      buildNav();
      await route();
      return true;
    } catch {
      clearToken();
      return false;
    }
  }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    setToken(document.getElementById("token").value.trim());
    errorEl.textContent = "";
    if (!(await tryStart())) errorEl.textContent = "unknown or disabled actor token";
  });

  document.getElementById("logout").addEventListener("click", () => {
    clearToken(); location.reload();
  });

  window.addEventListener("hashchange", route);
  document.getElementById("quick-search").addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      location.hash = `#/search?q=${encodeURIComponent(e.target.value)}`;
    }
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "/" && !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)) {
      e.preventDefault();
      document.getElementById("quick-search").focus();
    }
  });

  if (!(await tryStart())) {
    login.classList.remove("hidden");
  }
}

boot();
