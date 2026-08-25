// The reading surfaces.

import { get } from "./api.js";
import { showClaimEvidence, showEntity } from "./evidence.js";
import { el, empty, epistemic, handle, humanPredicate, humanSubject, ident,
         markingChip, pips, skeleton, stateWord, when } from "./render.js";

const page = (...kids) => el("div", { class: "page" }, ...kids);

/** Group claims by the thing they are about. A subject, not a record type. */
function bySubject(claims) {
  const groups = new Map();
  for (const c of claims) {
    const key = c.subject_ref || c.subject_object_id || "(unattributed)";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(c);
  }
  return groups;
}

/** A tension is the SAME subject asserting the SAME predicate with different
 *  current values. Anything looser invents disagreement that the evidence does
 *  not support -- two different predicates saying different things is simply
 *  two facts, and presenting that as a conflict would be a lie the interface
 *  tells on the store's behalf. */
function tensions(claims) {
  const out = [];
  const byPred = new Map();
  for (const c of claims) {
    const norm = String(c.predicate || "").toLowerCase();
    if (!byPred.has(norm)) byPred.set(norm, []);
    byPred.get(norm).push(c);
  }
  for (const [, group] of byPred) {
    const values = new Set(group.map(c => String(c.object_or_value ?? "").trim()).filter(Boolean));
    if (group.length > 1 && values.size > 1) out.push(group);
  }
  return out;
}

function claimButton(c) {
  const st = epistemic(c);
  const value = String(c.object_or_value ?? "");
  const btn = el("button", {
    class: `claim ${st.cls}`, type: "button", "aria-selected": "false",
    onclick: () => showClaimEvidence(c.claim_id, btn),
  },
    el("div", { class: "label" }, humanPredicate(c.predicate)),
    el("div", { class: `value ${value.length > 48 ? "long" : ""}` }, value || "—"),
    el("div", { class: "whisper" },
      pips(c.independent_basis_count),
      stateWord(st.word, st.tone),
      c.recorded_time ? el("span", {}, when(c.recorded_time)) : null,
      markingChip(c.marking),
      ident(c.claim_id)));
  return btn;
}

export async function overview(main) {
  main.replaceChildren(page(skeleton()));
  const d = await get("/api/overview");
  const c = d.counts || {};
  const meta = d.meta || {};
  main.replaceChildren(page(
    el("div", { class: "subject-head" },
      el("div", { class: "eyebrow" }, "Mission"),
      el("h1", { class: "title" },
        meta.mission_question || (d.objectives || [])[0]?.statement || "Mission overview"),
      el("p", { class: "subtitle" },
        `${c.claims ?? 0} claims · ${c.entities ?? 0} entities · ${c.relationships ?? 0} relations · ${c.sources ?? 0} sources`),
      ident(meta.store_id)),

    el("h2", { class: "section" }, "What is being claimed"),
    el("div", { id: "ov-claims" }, skeleton()),

    el("h2", { class: "section" }, "Open questions"),
    (d.hypotheses || []).length
      ? el("div", { class: "rows" }, ...(d.hypotheses || []).map(h =>
          el("button", { class: "row", type: "button" },
            el("span", { class: "primary" }, h.statement || h.label || "hypothesis"),
            el("span", { class: "meta" }, stateWord(String(h.status || "open").toLowerCase(), "open")))))
      : empty("no hypotheses recorded"),

    el("h2", { class: "section" }, "Where it came from"),
    el("div", { id: "ov-sources" }, skeleton())));

  // claims
  try {
    const claims = await loadClaims();
    const host = main.querySelector("#ov-claims");
    host.replaceChildren();
    const groups = bySubject(claims);
    for (const [subject, group] of groups) {
      if (group.length === 0) continue;
      host.append(el("div", { class: "eyebrow subject-group" },
        humanSubject(subject),
        el("span", { class: "count" }, `${group.length} claim${group.length === 1 ? "" : "s"}`)));
      for (const t of tensions(group)) {
        host.append(el("div", { class: "tension-block" },
          el("h3", {}, "Unresolved tension"),
          el("p", {}, t.map(x => `“${x.object_or_value}”`).join("  vs  ")),
          el("div", { class: "whisper" },
            "Two captures disagree. Neither is retracted; the relationship between them is not established by captured evidence.")));
      }
      group.forEach(cl => host.append(claimButton(cl)));
    }
    if (!groups.size) host.replaceChildren(empty("no claims yet"));
  } catch (err) {
    main.querySelector("#ov-claims").replaceChildren(el("p", { class: "error" }, err.message));
  }

  // sources
  try {
    const srcs = await loadFamily("fabric_source_descriptor");
    const host = main.querySelector("#ov-sources");
    host.replaceChildren(srcs.length ? el("div", { class: "rows" }, ...srcs.map(s =>
      el("button", { class: "row", type: "button" },
        el("span", { class: `primary ${s.official_status === "OFFICIAL" ? "official" : ""}` },
          s.publisher_name || s.source_id),
        el("span", { class: "meta" },
          s.official_status === "OFFICIAL" ? stateWord("official", "settled") : stateWord("unofficial", "muted"),
          ident(s.source_id))))) : empty("no sources registered"));
  } catch { /* non-fatal */ }
}

async function loadFamily(kind) {
  const d = await get(`/api/family/${encodeURIComponent(kind)}`);
  return d.items || d.records || (Array.isArray(d) ? d : []);
}
async function loadClaims() { return loadFamily("semantic_claim"); }

export async function claims(main) {
  main.replaceChildren(page(skeleton()));
  const all = await loadClaims();
  const groups = bySubject(all);
  const body = page(
    el("div", { class: "subject-head" },
      el("div", { class: "eyebrow" }, "Claims"),
      el("h1", { class: "title" }, "What the evidence says"),
      el("p", { class: "subtitle" },
        `${all.length} claims about ${groups.size} subject${groups.size === 1 ? "" : "s"} — select one to follow it down to the captured bytes`)));
  for (const [subject, group] of groups) {
    body.append(el("h2", { class: "section" }, humanSubject(subject)));
    for (const t of tensions(group)) {
      body.append(el("div", { class: "tension-block" },
        el("h3", {}, "Unresolved tension"),
        el("p", {}, t.map(x => `“${x.object_or_value}”`).join("  vs  ")),
        el("div", { class: "whisper" }, "Two captures disagree; neither is retracted.")));
    }
    group.forEach(c => body.append(claimButton(c)));
  }
  if (!groups.size) body.append(empty("no claims"));
  main.replaceChildren(body);
}

export async function entities(main) {
  main.replaceChildren(page(skeleton()));
  const d = await get("/api/entities?limit=400");
  const list = d.entities || [];
  const named = list.filter(e => (e.labels || []).length);
  const unnamed = list.length - named.length;
  main.replaceChildren(page(
    el("div", { class: "subject-head" },
      el("div", { class: "eyebrow" }, "Entities"),
      el("h1", { class: "title" }, "Things in the world model"),
      el("p", { class: "subtitle" },
        `${list.length} objects — ${named.length} named${unnamed ? `, ${unnamed} not yet named` : ""}`)),
    named.length ? el("div", { class: "rows" }, ...named.map(e =>
      el("button", { class: "row", type: "button", onclick: () => showEntity(e.object_id) },
        el("span", { class: "primary" }, (e.labels || [])[0]),
        el("span", { class: "meta" },
          el("span", {}, String(e.object_type || "").toLowerCase().replace(/_/g, " ")),
          e.history_count > 1 ? el("span", {}, `${e.history_count} versions`) : null,
          ident(e.object_id))))) : empty("no named entities")));
}

export async function sources(main) {
  main.replaceChildren(page(skeleton()));
  const [srcs, mans] = await Promise.all([loadFamily("fabric_source_descriptor"), loadFamily("fabric_manifestation")]);
  const byId = new Map();
  for (const m of mans) {
    const k = m.source_id || "(unknown)";
    if (!byId.has(k)) byId.set(k, []);
    byId.get(k).push(m);
  }
  const body = page(
    el("div", { class: "subject-head" },
      el("div", { class: "eyebrow" }, "Collection"),
      el("h1", { class: "title" }, "What was actually captured"),
      el("p", { class: "subtitle" }, `${mans.length} captures from ${srcs.length} registered sources`)));
  for (const s of srcs) {
    const captures = byId.get(s.source_id) || [];
    body.append(el("h2", { class: "section" }, s.publisher_name || s.source_id));
    body.append(captures.length ? el("div", { class: "rows" }, ...captures.map(m =>
      el("button", { class: "row", type: "button" },
        el("span", { class: "primary" }, m.native_id || m.final_url || "(capture)"),
        el("span", { class: "meta" },
          m.archive_capture_time
            ? el("span", {}, `archived ${when(m.archive_capture_time, { full: true })}`)
            : el("span", {}, when(m.retrieval_time)),
          m.http_status ? stateWord(String(m.http_status), m.http_status === 200 ? "settled" : "tension") : null,
          ident(m.manifestation_id)))))
      : empty("registered, nothing captured yet"));
  }
  main.replaceChildren(body);
}

export async function relations(main) {
  main.replaceChildren(page(skeleton()));
  const g = await get("/api/graph");
  const label = new Map((g.nodes || []).map(n => [n.object_id,
    (n.label && !/^wm-/.test(n.label)) ? n.label : null]));
  const named = (id) => label.get(id) || `unnamed ${String(id).slice(-6)}`;

  const byType = new Map();
  for (const e of g.edges || []) {
    const t = e.relation_type || "related";
    if (!byType.has(t)) byType.set(t, []);
    byType.get(t).push(e);
  }
  const open = (g.association_proposals || []).filter(p => p.open);

  const body = page(
    el("div", { class: "subject-head" },
      el("div", { class: "eyebrow" }, "Relations"),
      el("h1", { class: "title" }, "How things connect"),
      el("p", { class: "subtitle" },
        `${(g.edges || []).length} relations of ${byType.size} kind${byType.size === 1 ? "" : "s"} across ${(g.nodes || []).length} objects`)));

  if (open.length) {
    body.append(el("div", { class: "tension-block" },
      el("h3", {}, `Identity unresolved \u2014 ${open.length} open`),
      el("p", {}, open.map(p => `${named(p.source)}  \u225F  ${named(p.target)}`).join("   \u00B7   ")),
      el("div", { class: "whisper" },
        "These may be the same entity. Curun\u00EDr will not collapse them without a human decision, so both remain in the world model.")));
  }

  // 108 rows of "AAPL -> unnamed 488064" is the hairball in list form. Group
  // by what the relation actually connects, and let a person open the detail.
  const kindOf = new Map((g.nodes || []).map(n => [n.object_id, n.object_type]));
  const human = (t) => String(t || "object").toLowerCase().replace(/_/g, " ");

  for (const [type, group] of [...byType.entries()].sort((a, b) => b[1].length - a[1].length)) {
    body.append(el("h2", { class: "section" }, `${humanPredicate(type)} · ${group.length}`));
    const buckets = new Map();
    for (const e of group) {
      const key = `${e.source}\u0000${kindOf.get(e.target) || "OBJECT"}`;
      if (!buckets.has(key)) buckets.set(key, []);
      buckets.get(key).push(e);
    }
    const rows = el("div", { class: "rows" });
    for (const [key, edges] of [...buckets.entries()].sort((a, b) => b[1].length - a[1].length)) {
      const [src, targetKind] = key.split("\u0000");
      const n = edges.length;
      const detail = el("div", { class: "hidden" });
      const row = el("button", { class: "row", type: "button", onclick: () => {
        detail.classList.toggle("hidden");
        if (!detail.childElementCount) {
          for (const e of edges.slice(0, 60)) {
            detail.append(el("button", { class: "row", type: "button",
              onclick: (ev) => { ev.stopPropagation(); showEntity(e.target); } },
              el("span", { class: "primary" }, named(e.target)),
              el("span", { class: "meta" }, ident(e.relationship_id))));
          }
          if (edges.length > 60) detail.append(el("p", { class: "whisper", style: "padding:8px" },
            `${edges.length - 60} more`));
        }
      } },
        el("span", { class: "primary" }, named(src)),
        el("span", { class: "meta" },
          el("span", {}, `${humanPredicate(type).toLowerCase()} ${n} ${human(targetKind)}${n === 1 ? "" : "s"}`),
          el("span", { class: "count" }, String(n))));
      rows.append(row, detail);
    }
    body.append(rows);
  }
  main.replaceChildren(body);
}
