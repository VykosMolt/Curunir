// Render primitives for the Quiet Instrument surface.
//
// The whole point of this module is the translation layer: the store speaks in
// predicates, record types and identifiers; a human reads sentences, words and
// values. Nothing here invents meaning — it only chooses what to put first.

export function el(tag, attrs = {}, ...kids) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "html") node.innerHTML = v;
    else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? "" : String(v));
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    node.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return node;
}

/** `registration_status` -> `Registration status`; `HEADER_TEXT` -> `Header text`. */
export function humanPredicate(p) {
  if (!p) return "";
  const words = String(p).replace(/[_-]+/g, " ").trim().toLowerCase();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** A subject reference the analyst can read. `LEI:HWUP…` keeps its scheme. */
export function humanSubject(ref, label) {
  if (label) return label;
  if (!ref) return "unknown subject";
  const [scheme, rest] = String(ref).split(/:(.+)/);
  if (rest && /^(URL|https?)$/i.test(scheme)) return rest.replace(/^\/\//, "");
  return ref;
}

/** Short, stable handle — the last 6 of an id, never the whole hash. */
export function handle(id) {
  const s = String(id || "");
  const tail = s.split("-").pop() || s;
  return tail.slice(-6);
}

/** Independent basis as pips. Legible with no colour at all. */
export function pips(count, max = 3) {
  const n = Math.max(0, Math.min(Number(count) || 0, max));
  return el("span", { class: "pips", title: `${count} independent source${count === 1 ? "" : "s"}` },
    ...Array.from({ length: max }, (_, i) => el("i", { class: i < n ? "on" : "" })));
}

export function ident(id) { return el("code", { class: "ident" }, id); }
export function dot() { return el("span", { class: "dot" }); }

/** Epistemic state -> the WORD a human reads, plus a reinforcing class. */
export function epistemic(claim) {
  const review = claim.review_state, state = claim.epistemic_state;
  if (review === "SUPERSEDED" || state === "SUPERSEDED") return { word: "superseded", cls: "is-historic", tone: "muted" };
  if (review === "CONTESTED" || state === "CONTESTED") return { word: "contested", cls: "is-tension", tone: "tension" };
  if (state === "INFERRED") return { word: "inferred", cls: "is-open", tone: "open" };
  if (review === "REVIEWED" || review === "CONFIRMED") return { word: "reviewed", cls: "is-settled", tone: "settled" };
  if (review === "UNREVIEWED") return { word: "unreviewed", cls: "", tone: "muted" };
  return { word: String(review || state || "recorded").toLowerCase(), cls: "", tone: "muted" };
}

export function stateWord(word, tone) { return el("span", { class: `state ${tone || "muted"}` }, word); }

/** A marking is shown only when it actually restricts something. */
export function markingChip(marking) {
  if (!marking) return null;
  const parts = [...(marking.compartments || [])];
  if (marking.min_role && marking.min_role !== "OBSERVER") parts.push(marking.min_role);
  const rel = marking.releasability || [];
  if (!parts.length && (rel.length === 0 || (rel.length === 1 && rel[0] === "PUBLIC"))) return null;
  return el("span", { class: "marking" }, parts.length ? parts.join(" · ") : rel.join(" · "));
}

export function when(iso, opts = {}) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(+d)) return String(iso).slice(0, 10);
  return d.toLocaleDateString(undefined,
    opts.full ? { year: "numeric", month: "long", day: "numeric" }
              : { year: "numeric", month: "short", day: "numeric" });
}

export function empty(text) { return el("p", { class: "empty" }, text); }
export function skeleton(text = "loading…") { return el("p", { class: "skeleton" }, text); }
