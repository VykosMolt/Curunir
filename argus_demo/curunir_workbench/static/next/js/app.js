// Shell and router.
//
// Six destinations, not twenty-four. Each answers a question an analyst
// actually arrives with, rather than naming a record type in the store.

import { ApiError, get, setToken, clearToken, token } from "./api.js";
import { setSession, session } from "./session.js";
import { closeAside, mountAside } from "./evidence.js";
import { el } from "./render.js";
import * as views from "./views.js";

const ROUTES = [
  { id: "overview",  label: "Overview",   group: "Mission",     view: views.overview },
  { id: "claims",    label: "Claims",     group: "The world",   view: views.claims,    count: "claims" },
  { id: "entities",  label: "Entities",   group: "The world",   view: views.entities,  count: "entities" },
  { id: "relations", label: "Relations",  group: "The world",   view: views.relations, count: "relationships" },
  { id: "sources",   label: "Collection", group: "Evidence",    view: views.sources,   count: "manifestations" },
];

let counts = {};

function renderRail(active) {
  const rail = document.querySelector("#rail nav");
  rail.replaceChildren();
  let group = null;
  for (const r of ROUTES) {
    if (r.group !== group) { group = r.group; rail.append(el("div", { class: "group" }, group)); }
    rail.append(el("a", {
      class: "dest", href: `#/${r.id}`,
      "aria-current": r.id === active ? "page" : null,
    }, r.label, r.count && counts[r.count] !== undefined
        ? el("span", { class: "n" }, String(counts[r.count])) : null));
  }
}

async function route() {
  const id = (location.hash.replace(/^#\/?/, "") || "overview").split("/")[0];
  const found = ROUTES.find(r => r.id === id) || ROUTES[0];
  closeAside();
  renderRail(found.id);
  const main = document.getElementById("main");
  try { await found.view(main); }
  catch (err) {
    if (err instanceof ApiError && err.status === 401) return showLogin("session expired — sign in again");
    main.replaceChildren(el("div", { class: "page" }, el("p", { class: "error" }, err.message)));
  }
  main.focus();
}

function showLogin(message = "") {
  document.getElementById("shell").classList.add("hidden");
  const login = document.getElementById("login");
  login.classList.remove("hidden");
  document.getElementById("login-error").textContent = message;
  document.getElementById("token")?.focus();
}

async function enter() {
  try {
    setSession(await get("/api/session"));
    try { counts = (await get("/api/overview")).counts || {}; } catch { counts = {}; }
  } catch (err) { return showLogin(err.status === 401 ? "that token was not accepted" : err.message); }
  document.getElementById("login").classList.add("hidden");
  document.getElementById("shell").classList.remove("hidden");
  document.querySelector("#rail .brand small").textContent =
    `${session.actor_id} · ${(session.roles || []).join(", ").toLowerCase()}`;
  await route();
}

/* ---- theme and auditor mode --------------------------------------------- */
const root = document.documentElement;
function setTheme(v) { v ? root.setAttribute("data-theme", v) : root.removeAttribute("data-theme");
  try { v ? localStorage.setItem("curunir-theme", v) : localStorage.removeItem("curunir-theme"); } catch {} }
function toggleTheme() {
  const now = root.getAttribute("data-theme");
  setTheme(now === "dark" ? "light" : now === "light" ? null : "dark");
}
function toggleAuditor() {
  const on = root.getAttribute("data-auditor") === "on";
  root.setAttribute("data-auditor", on ? "off" : "on");
  try { localStorage.setItem("curunir-auditor", on ? "off" : "on"); } catch {}
}
try {
  const t = localStorage.getItem("curunir-theme"); if (t) setTheme(t);
  if (localStorage.getItem("curunir-auditor") === "on") root.setAttribute("data-auditor", "on");
} catch {}

addEventListener("keydown", (e) => {
  if (e.target.matches("input, textarea")) return;
  if (e.key === "Escape") closeAside();
  if (e.key === "a" && !e.metaKey && !e.ctrlKey) toggleAuditor();
  if (e.key === "t" && !e.metaKey && !e.ctrlKey) toggleTheme();
  const n = Number(e.key);
  if (n >= 1 && n <= ROUTES.length) location.hash = `#/${ROUTES[n - 1].id}`;
});

addEventListener("hashchange", route);
addEventListener("DOMContentLoaded", () => {
  mountAside(document.getElementById("aside"));
  document.getElementById("login-form").addEventListener("submit", (e) => {
    e.preventDefault();
    setToken(document.getElementById("token").value.trim());
    enter();
  });
  document.getElementById("signout").addEventListener("click", () => { clearToken(); location.reload(); });
  document.getElementById("auditor-toggle").addEventListener("click", toggleAuditor);
  document.getElementById("theme-toggle").addEventListener("click", toggleTheme);
  token() ? enter() : showLogin();
});
