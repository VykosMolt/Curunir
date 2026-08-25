// Workbench views, part 1: mission shell, world model, evidence, provenance.
// Every view renders an authorized projection fetched from the server; the
// browser derives nothing and owns no mission truth.
import { get, post, ApiError } from "./api.js";
import {
  annotationList, badge, chainNode, claimDescentView, clip, emptyBox, errorBox,
  fmtTime, graphSvg, h, kv, loading, mapSvg, pivot, probabilityChart,
  recordRoute, refLink, table, ident,
} from "./ui.js";

export function nav(route) { location.hash = `#${route}`; }

// save the current layout (filters/focus/window) as a named mission view
export function saveViewButton(viewKind, definitionFn) {
  const status = h("span", {});
  return h("span", {},
    h("button", { onclick: async () => {
      const title = prompt("save this view as:");
      if (!title) return;
      try {
        await post("/api/commands/saved-views", {
          title, view_kind: viewKind, definition: definitionFn() });
        status.replaceChildren(h("span", { class: "faint" }, ` saved “${title}”`));
      } catch (err) { status.replaceChildren(errorBox(err)); }
    } }, "Save view"), status);
}

export async function render(main, fn) {
  main.replaceChildren(loading());
  try {
    const content = await fn();
    main.replaceChildren(...[content].flat(Infinity).filter(Boolean));
  } catch (err) {
    main.replaceChildren(errorBox(err));
  }
}

// annotate box reused across detail views
export function annotateBox(targetKind, targetId, refresh, { anchorRef = "" } = {}) {
  const textarea = h("textarea", { placeholder: "annotation…", "aria-label": "annotation text" });
  const kind = h("select", { "aria-label": "annotation kind" },
    ["NOTE", "QUESTION", "DISSENT", "CORRECTION_SUGGESTION"].map((k) => h("option", {}, k)));
  const status = h("span", {});
  return h("div", { class: "card" },
    h("h3", {}, "Annotate"),
    textarea,
    h("div", { class: "toolbar" }, kind,
      h("button", { class: "primary", onclick: async () => {
        try {
          await post("/api/commands/annotate", {
            target_kind: targetKind, target_id: targetId, kind: kind.value,
            text: textarea.value, anchor_ref: anchorRef });
          textarea.value = "";
          refresh();
        } catch (err) { status.replaceChildren(errorBox(err)); }
      } }, "Add"), status));
}

export function annotationsSection(annotations, targetKind, targetId, refresh) {
  return [h("h2", {}, `Annotations (${annotations.length})`),
    annotationList(annotations, { onResolve: async (a, s) => {
      const note = prompt(`${s} note:`) || "";
      if (!note) return;
      try {
        await post(`/api/commands/annotations/${a.annotation_id}/resolve`,
          { expected_version: a.version, status: s, note });
        refresh();
      } catch (err) { alert(err.message); }
    } }),
    annotateBox(targetKind, targetId, refresh)];
}

// ---- overview ---------------------------------------------------------------

export async function overviewView(main) {
  await render(main, async () => {
    const ov = await get("/api/overview");
    const c = ov.counts;
    const stat = (label, value, route) =>
      h("span", {}, route ? pivot(route, `${label}: `) : `${label}: `, h("b", {}, value));
    const itemCard = (title, items, renderItem, emptyMsg, route) =>
      h("div", { class: "card" },
        h("h2", {}, route ? pivot(route, title) : title),
        items.length ? items.map(renderItem) : emptyBox(emptyMsg));
    return [
      h("h1", {}, "Common operating picture"),
      // The mission, in the operator's words. Store, state token and context
      // are custody detail: real, retained, and shown in Auditor mode only.
      h("p", { class: "muted" },
        `as at ${fmtTime(ov.meta.snapshot_time)}`,
        h("code", { class: "ident" },
          ` store ${ov.meta.store_id} · state ${ov.meta.state_token} · context ${ov.meta.context_id}`)),
      h("div", { class: "statline" },
        stat("entities", c.entities, "/entities"), stat("events", c.events, "/events"),
        stat("claims", c.claims), stat("evidence", c.manifestations, "/evidence"),
        stat("sources", c.sources, "/sources"), stat("themes", c.themes, "/themes"),
        stat("hypotheses", c.hypotheses, "/hypotheses"),
        stat("forecasts open", c.forecasts_open, "/forecasts"),
        stat("warnings", c.warnings_active, "/warnings"),
        stat("requirements", c.requirements_open, "/investigation"),
        stat("tasks", c.tasks_open, "/tasks"), stat("review", c.review_open, "/review"),
        stat("watches", c.watches_active, "/watches"),
        stat("coverage gaps", c.coverage_gaps, "/collection"),
        stat("reports", c.reports, "/reports")),
      h("div", { class: "cop" },
        itemCard("Mission objectives", ov.objectives, (o) =>
          h("div", {}, badge(o.priority), refLink("mission_objective", o.objective_id, clip(o.statement, 100))),
          "no objectives recorded", "/impact"),
        itemCard("Active warnings", ov.active_warnings, (w) =>
          h("div", {}, badge(w.tier, "tier"), badge(w.probability_band),
            refLink("strategic_warning", w.warning_id, clip(w.mission_context || w.warning_id, 80))),
          "no active warnings", "/warnings"),
        itemCard("Forecasts near horizon", ov.forecasts_near_horizon, (f) =>
          h("div", {}, h("b", { class: "mono" }, Number(f.probability).toFixed(2)), " ",
            refLink("analytic_forecast", f.forecast_id, clip(f.question, 90)),
            h("div", { class: "sub faint" }, `horizon ${fmtTime(f.horizon_time)} · by ${f.author}`)),
          "none approaching", "/forecasts"),
        itemCard("Key hypotheses", ov.hypotheses, (hy) =>
          h("div", {}, badge(hy.status),
            refLink("hypothesis", hy.hypothesis_id, clip(hy.statement, 90))),
          "no hypotheses", "/hypotheses"),
        itemCard("Open information requirements", ov.open_requirements, (r) =>
          h("div", {}, badge(r.priority), badge(r.status), " ", clip(r.question, 90)),
          "none open", "/investigation"),
        itemCard("Unresolved review / contradictions", ov.open_review_items, (r) =>
          h("div", {}, badge(r.kind), " ", clip(r.detail, 90)),
          "review queue clear", "/review"),
        itemCard("Themes", ov.themes, (t) =>
          h("div", {}, badge(t.status),
            refLink("analytic_theme", t.theme_id, clip(t.title || t.theme_id, 90))),
          "no themes", "/themes"),
        itemCard("Armed indicators", ov.armed_indicators, (i) =>
          h("div", {}, badge(i.status), badge(i.indicator_type || i.kind), " ",
            clip(i.description || i.indicator_id, 80)),
          "none armed", "/indicators"),
        itemCard("Open analyst tasks", ov.open_tasks, (t) =>
          h("div", {}, badge(t.status), t.overdue ? badge("OVERDUE") : null, " ",
            clip(t.required_action, 80),
            h("div", { class: "sub faint" }, `→ ${t.assigned_actor}`)),
          "no open tasks", "/tasks"),
        itemCard("Collection in progress / pending routes", ov.pending_routes, (r) =>
          h("div", {}, badge(r.status), " ", clip(r.explanation || r.query_value, 90)),
          "no pending routes", "/collection"),
        itemCard("Coverage gaps", ov.coverage_gaps, (g) =>
          h("div", {}, badge(g.state), " ", `${g.source_family}`,
            h("span", { class: "faint" }, ` — ${clip((g.gaps || []).join("; "), 70)}`)),
          "no known gaps (within assessed needs)", "/collection"),
        itemCard("Recent semantic change", ov.recent_semantic_changes, (ch) =>
          h("div", {}, badge(ch.change_class), " ", clip(ch.detail, 90),
            h("div", { class: "sub faint" }, fmtTime(ch.recorded_time))),
          "no interpreted changes", "/timeline"),
        itemCard("Recent evidence", ov.recent_evidence, (m) =>
          h("div", {}, refLink("fabric_manifestation", m.manifestation_id,
            clip(m.final_url || m.native_id, 80)),
            h("div", { class: "sub faint" }, `${m.source_id} · ${fmtTime(m.retrieval_time)}`)),
          "no evidence yet", "/evidence"),
        itemCard("Open alerts", ov.alerts, (a) =>
          h("div", {}, badge(a.severity), " ", clip(a.trigger, 90)),
          "no open alerts", "/activity")),
    ];
  });
}

// ---- search -----------------------------------------------------------------

export async function searchView(main, params) {
  const q = params.get("q") || "";
  const input = h("input", { type: "search", value: q, "aria-label": "search query",
    placeholder: "search typed mission objects…" });
  const go = () => nav(`/search?q=${encodeURIComponent(input.value)}`);
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
  const shellEl = [h("h1", {}, "Search"),
    h("div", { class: "toolbar" }, input, h("button", { class: "primary", onclick: go }, "Search"))];
  if (!q) { main.replaceChildren(...shellEl, emptyBox("type a query")); return; }
  await render(main, async () => {
    const res = await get(`/api/search?q=${encodeURIComponent(q)}`);
    return [...shellEl,
      h("p", { class: "muted" }, `${res.total} results (showing ${res.results.length})`),
      table({
        columns: [
          { label: "type", render: (r) => badge(r.type) },
          { label: "match", render: (r) => refLink(r.type, r.id, clip(r.label, 110)) },
          { label: "status", render: (r) => [badge(r.status), badge(r.epistemic_state)] },
          { label: "matched fields", render: (r) => h("span", { class: "faint" }, r.matched_fields.join(", ")) },
        ],
        rows: res.results, empty: "no visible results",
      })];
  });
}

// ---- activity ---------------------------------------------------------------

export async function activityView(main) {
  await render(main, async () => {
    const res = await get("/api/activity?limit=300");
    return [h("h1", {}, "Mission activity"),
      h("p", { class: "muted" }, "attributable actions over visible records, newest first"),
      table({
        columns: [
          { label: "time", render: (e) => h("span", { class: "mono" }, fmtTime(e.time)) },
          { label: "actor", key: "actor" },
          { label: "event", render: (e) => h("span", { class: "mono" }, e.event_type) },
          { label: "record", render: (e) => e.record_id
              ? refLink(e.record_type, e.record_id) : h("span", { class: "faint" }, "—") },
          { label: "v", key: "version" },
          { label: "status", render: (e) => badge(e.status) },
        ],
        rows: res.feed,
      })];
  });
}

// ---- entities ---------------------------------------------------------------

export async function entitiesView(main) {
  await render(main, async () => {
    const res = await get("/api/entities");
    return [h("h1", {}, "Entities"),
      table({
        columns: [
          { label: "label", render: (e) => pivot(`/entities/${e.object_id}`,
              (e.labels || [e.object_id])[0]) },
          { label: "type", render: (e) => badge(e.object_type) },
          { label: "epistemic", render: (e) => badge(e.epistemic_state) },
          { label: "freshness", render: (e) => badge(e.freshness) },
          { label: "versions", key: "history_count" },
          { label: "identity", render: (e) => e.cluster_partially_hidden
              ? h("span", { class: "badge unknown",
                            title: "this entity's identity cluster includes objects you cannot view" },
                  "linkage partly hidden") : "" },
          { label: "geo", render: (e) => e.has_geometry ? "◈" : "" },
        ],
        rows: res.entities,
      })];
  });
}

export async function entityView(main, params, id) {
  const refresh = () => entityView(main, params, id);
  await render(main, async () => {
    const d = await get(`/api/entities/${encodeURIComponent(id)}`);
    const cur = d.current;
    return [
      h("h1", {}, (cur.labels || [id])[0], " ",
        badge(cur.object_type), badge(cur.epistemic_state), badge(cur.lifecycle)),
      h("p", { class: "faint mono" }, id),
      d.identity_proposals.length ? h("div", { class: "notice warn" },
        `identity ambiguity: ${d.identity_proposals.length} unresolved/recorded association proposal(s) — `,
        pivot("/review", "review queue")) : null,
      kv([
        ["current since (valid)", fmtTime(cur.valid_from)],
        ["source time", fmtTime(cur.source_time)],
        ["recorded", fmtTime(cur.recorded_time)],
        ["time precision", cur.time_precision],
        ["freshness", badge(cur.freshness?.state)],
        ["quality", badge(cur.quality_summary?.state)],
        ["cluster", cur.cluster_id !== cur.object_id ? cur.cluster_id : ""],
        ["attributes", h("span", { class: "mono" }, clip(JSON.stringify(cur.attributes || {}), 300))],
      ]),
      h("h2", {}, `Version history (${d.history.length}) — historical ≠ current`),
      table({
        columns: [
          { label: "v", key: "version" },
          { label: "valid from", render: (r) => fmtTime(r.valid_from) },
          { label: "recorded", render: (r) => fmtTime(r.recorded_time) },
          { label: "epistemic", render: (r) => badge(r.epistemic_state) },
          { label: "labels", render: (r) => (r.labels || []).join(", ") },
          { label: "attributes", render: (r) => h("span", { class: "mono faint" },
              clip(JSON.stringify(r.attributes || {}), 120)) },
        ],
        rows: [...d.history].reverse(),
      }),
      h("h2", {}, `Claims (${d.claims.length})`),
      table({
        columns: [
          { label: "statement", render: (r) => refLink("semantic_claim", r.claim_id, clip(r.statement, 110)) },
          { label: "state", render: (r) => [badge(r.state), badge(r.epistemic_state)] },
          { label: "independent basis", key: "independent_basis_count" },
          { label: "valid", render: (r) => `${fmtTime(r.valid_from)}${r.valid_to ? " → " + fmtTime(r.valid_to) : ""}` },
        ],
        rows: d.claims,
      }),
      h("h2", {}, `Relationships (${d.relationships.length})`),
      table({
        columns: [
          { label: "relation", render: (r) => badge(r.relation_type) },
          { label: "from", render: (r) => refLink("object", r.source_object_id) },
          { label: "to", render: (r) => refLink("object", r.target_object_id) },
          { label: "status", render: (r) => badge(r.status) },
        ],
        rows: d.relationships,
      }),
      h("h2", {}, `Events (${d.events.length})`),
      table({
        columns: [
          { label: "event", render: (r) => refLink("activity", r.activity_id, r.activity_type) },
          { label: "description", render: (r) => clip(r.description, 90) },
          { label: "valid", render: (r) => fmtTime(r.valid_from) },
          { label: "epistemic", render: (r) => badge(r.epistemic_state) },
        ],
        rows: d.events,
      }),
      d.contradictions.length ? [h("h2", {}, "Contradictions / review"),
        table({ columns: [
          { label: "kind", render: (r) => badge(r.kind) },
          { label: "detail", render: (r) => clip(r.detail, 120) },
          { label: "status", render: (r) => badge(r.status) }], rows: d.contradictions })] : null,
      d.forecasts.length ? [h("h2", {}, "Forecasts involving this entity"),
        d.forecasts.map((f) => h("div", { class: "card" },
          h("b", { class: "mono" }, Number(f.probability).toFixed(2)), " ",
          refLink("analytic_forecast", f.forecast_id, clip(f.question, 110)), " ",
          badge(f.status)))] : null,
      d.themes.length ? [h("h2", {}, "Themes"),
        d.themes.map((t) => h("div", {}, refLink("analytic_theme", t.theme_id, t.title || t.theme_id),
          " ", badge(t.status)))] : null,
      d.stakeholder_assessments.length ? [h("h2", {}, "Stakeholder assessments"),
        d.stakeholder_assessments.map((s) => h("div", {},
          refLink("stakeholder_assessment", s.assessment_id, clip(s.context_ref || s.assessment_id, 80))))] : null,
      annotationsSection(d.annotations, "object", id, refresh),
    ];
  });
}

// ---- events -----------------------------------------------------------------

export async function eventsView(main) {
  await render(main, async () => {
    const res = await get("/api/events");
    return [h("h1", {}, "Events"),
      table({
        columns: [
          { label: "type", render: (e) => refLink("activity", e.activity_id, e.activity_type) },
          { label: "description", render: (e) => clip(e.description, 110) },
          { label: "valid time", render: (e) => [fmtTime(e.valid_from || e.source_time) || h("span", { class: "faint" }, "unknown"),
              " ", badge(e.time_precision !== "EXACT" ? e.time_precision : "")] },
          { label: "recorded", render: (e) => fmtTime(e.recorded_time) },
          { label: "epistemic", render: (e) => badge(e.epistemic_state) },
        ],
        rows: res.events,
      })];
  });
}

export async function eventView(main, params, id) {
  const refresh = () => eventView(main, params, id);
  await render(main, async () => {
    const d = await get(`/api/events/${encodeURIComponent(id)}`);
    const cur = d.current;
    return [
      h("h1", {}, cur.activity_type, " ", badge(cur.epistemic_state)),
      h("p", {}, cur.description),
      kv([
        ["valid from", fmtTime(cur.valid_from) || "unknown"],
        ["time precision", badge(cur.time_precision)],
        ["source time", fmtTime(cur.source_time)],
        ["recorded (knowledge)", fmtTime(cur.recorded_time)],
        ["participants", h("span", {}, (d.participants || []).map(([pid, role]) =>
          h("span", {}, refLink("object", pid), h("span", { class: "faint" }, ` (${role}) `))))],
      ]),
      cur.time_precision && cur.time_precision !== "EXACT"
        ? h("div", { class: "notice" },
            `temporal precision is ${cur.time_precision} — the display does not imply exact-day knowledge`)
        : null,
      h("h2", {}, "Source observations"),
      d.observations.length ? d.observations.map((o) => h("div", { class: "card" },
        h("div", {}, badge(o.producer_kind), h("b", {}, o.attribute), " = ", clip(o.value, 160)),
        h("div", { class: "faint" }, refLink("semantic_observation", o.observation_id, "observation"),
          " · ", refLink("fabric_manifestation", o.manifestation_id, "manifestation"),
          " · source ", o.source_id))) : emptyBox("no visible observations"),
      d.claims.length ? [h("h2", {}, "Claims materialized from this event"),
        d.claims.map((c) => h("div", {}, refLink("semantic_claim", c.claim_id, clip(c.statement, 120))))] : null,
      annotationsSection(d.annotations, "activity", id, refresh),
    ];
  });
}

// ---- timeline ---------------------------------------------------------------

const TIMELINE_KINDS = ["object_version", "event", "manifestation", "semantic_change",
  "claim", "forecast", "indicator", "warning", "collection", "watch_run",
  "decision", "analyst_action", "annotation", "report"];

export async function timelineView(main, params) {
  const axis = params.get("axis") || "knowledge";
  const kinds = params.get("kinds") || "";
  await render(main, async () => {
    const res = await get(`/api/timeline?axis=${axis}&kinds=${encodeURIComponent(kinds)}`);
    const kindSelect = h("select", { multiple: true, size: 6, "aria-label": "timeline kinds" },
      TIMELINE_KINDS.map((k) => h("option", { value: k,
        selected: kinds.split(",").includes(k) || undefined }, k)));
    const apply = () => {
      const chosen = [...kindSelect.selectedOptions].map((o) => o.value).join(",");
      nav(`/timeline?axis=${axis}&kinds=${chosen}`);
    };
    return [
      h("h1", {}, "Timeline"),
      h("div", { class: "toolbar" },
        h("span", { class: "muted" }, "axis:"),
        ["valid", "knowledge"].map((a) =>
          h("button", { class: a === axis ? "primary" : "",
            onclick: () => nav(`/timeline?axis=${a}&kinds=${kinds}`) },
            a === "valid" ? "VALID TIME (world)" : "KNOWLEDGE TIME (Curunír)")),
        h("span", { class: "spacer" }),
        kindSelect, h("button", { onclick: apply }, "Filter"),
        saveViewButton("timeline", () => ({ axis, kinds }))),
      h("p", { class: "muted" }, axis === "valid"
        ? "what was happening in the world, at the time it was valid — items without a known valid time do not appear here"
        : "what Curunír learned, in the order it learned it"),
      table({
        columns: [
          { label: axis === "valid" ? "valid time" : "recorded", render: (e) =>
              h("span", { class: "mono" }, fmtTime(e.time),
                e.time_precision && e.time_precision !== "EXACT" ? [" ", badge(e.time_precision)] : "") },
          { label: "kind", render: (e) => badge(e.kind) },
          { label: "item", render: (e) => refLink(
              { object_version: "object", event: "activity", manifestation: "fabric_manifestation",
                semantic_change: "record/semantic_change", claim: "semantic_claim",
                forecast: "analytic_forecast", indicator: "forecast_indicator",
                warning: "strategic_warning", collection: "record/fabric_execution",
                watch_run: "record/fabric_watch_run", decision: "record/decision",
                analyst_action: "record/analyst_action", annotation: "workbench_annotation",
                report: "workbench_report" }[e.kind] || e.kind, e.ref, clip(e.label, 130)) },
          { label: "other axis", render: (e) => h("span", { class: "faint mono" },
              axis === "valid" ? `recorded ${fmtTime(e.recorded_time)}`
                : (e.valid_time ? `valid ${fmtTime(e.valid_time)}` : "no valid time")) },
        ],
        rows: res.entries.slice().reverse(),
      })];
  });
}

// ---- graph ------------------------------------------------------------------

export async function graphView(main, params) {
  const focus = params.get("focus") || "";
  const depth = params.get("depth") || "2";
  await render(main, async () => {
    const res = await get(`/api/graph?focus=${encodeURIComponent(focus)}&depth=${depth}`);
    const focusInput = h("input", { value: focus, placeholder: "focus object id…" });
    return [
      h("h1", {}, "Relationship graph"),
      h("div", { class: "toolbar" },
        focusInput,
        h("button", { onclick: () => nav(`/graph?focus=${encodeURIComponent(focusInput.value)}&depth=${depth}`) }, "Focus"),
        focus ? h("button", { onclick: () => nav("/graph") }, "Clear") : null,
        saveViewButton("graph", () => ({ focus, depth })),
        h("span", { class: "muted" },
          `${res.nodes.length} nodes · ${res.edges.length} typed edges · solid = evidentiary, dashed = asserted, dotted = identity proposal`)),
      graphSvg(res, { onNode: (n) => nav(`/entities/${n.object_id}`) }),
      h("h2", {}, "Edges"),
      table({
        columns: [
          { label: "relation", render: (e) => badge(e.relation_type) },
          { label: "from", render: (e) => refLink("object", e.source) },
          { label: "to", render: (e) => refLink("object", e.target) },
          { label: "basis", render: (e) => badge(e.basis) },
          { label: "status", render: (e) => badge(e.status) },
          { label: "rationale", render: (e) => h("span", { class: "faint" }, clip(e.rationale, 80)) },
        ],
        rows: res.edges,
      }),
      res.association_proposals.length ? [h("h2", {}, "Identity proposals (unmerged ambiguity)"),
        table({ columns: [
          { label: "left", render: (p) => refLink("object", p.source) },
          { label: "right", render: (p) => refLink("object", p.target) },
          { label: "outcome", render: (p) => badge(p.outcome) },
          { label: "state", render: (p) => badge(p.open ? "OPEN" : "RESOLVED") }],
          rows: res.association_proposals })] : null,
    ];
  });
}

// ---- map --------------------------------------------------------------------

export async function mapViewPage(main) {
  await render(main, async () => {
    const res = await get("/api/map");
    return [h("h1", {}, "Map"),
      h("p", { class: "muted" }, "evidence-backed coordinates only — nothing here is geocoded or invented"),
      mapSvg(res.feature_collection, { onFeature: (f) => nav(`/entities/${f.properties.object_id}`) }),
      res.unlocated.length ? [h("h2", {}, "Known but unlocated"),
        h("p", { class: "muted" }, "these objects have no evidence-backed coordinates; that absence is shown, not silently filled"),
        table({ columns: [
          { label: "object", render: (u) => refLink("object", u.object_id, u.label) },
          { label: "type", render: (u) => badge(u.object_type) }], rows: res.unlocated })] : null];
  });
}

// ---- evidence ---------------------------------------------------------------

export async function evidenceListView(main) {
  await render(main, async () => {
    const res = await get("/api/family/fabric_manifestation");
    return [h("h1", {}, "Evidence"),
      table({
        columns: [
          { label: "manifestation", render: (m) => refLink("fabric_manifestation",
              m.manifestation_id, clip(m.final_url || m.native_id, 90)) },
          { label: "source", render: (m) => refLink("fabric_source_descriptor", m.source_id, m.source_id) },
          { label: "status", render: (m) => [badge(m.temporal_status), m.http_status] },
          { label: "retrieved", render: (m) => fmtTime(m.retrieval_time) },
          { label: "sha256", render: (m) => h("span", { class: "mono faint" }, clip(m.content_sha256, 16)) },
        ],
        rows: res.records.slice().reverse(),
      })];
  });
}

function highlightedPayload(payload, anchors) {
  if (!payload || payload.text === null || payload.text === undefined) {
    return h("div", { class: "notice warn" }, "payload not available in this store view");
  }
  const spans = anchors
    .filter((a) => a.kind === "TEXT_SPAN" && a.start !== null && a.start !== undefined)
    .map((a) => [a.start, a.end])
    .sort((x, y) => x[0] - y[0]);
  const pre = h("pre", { class: "payload" });
  if (!spans.length) { pre.textContent = payload.text; return pre; }
  let cursor = 0;
  for (const [start, end] of spans) {
    if (start > cursor) pre.append(payload.text.slice(cursor, start));
    if (start >= cursor) {
      pre.append(h("mark", { class: "anchor" }, payload.text.slice(Math.max(start, cursor), end)));
      cursor = Math.max(cursor, end);
    }
  }
  pre.append(payload.text.slice(cursor));
  return pre;
}

export async function evidenceView(main, params, id) {
  const refresh = () => evidenceView(main, params, id);
  await render(main, async () => {
    const d = await get(`/api/evidence/${encodeURIComponent(id)}`);
    const m = d.manifestation;
    const fieldAnchors = d.anchors.filter((a) => a.field_path);
    return [
      h("h1", {}, "Evidence: ", clip(m.final_url || m.native_id, 70)),
      kv([
        ["source", refLink("fabric_source_descriptor", m.source_id, m.source_id)],
        ["native id", h("span", { class: "mono" }, m.native_id)],
        ["request URL", m.request_url], ["final URL", m.final_url],
        ["temporal status", badge(m.temporal_status)],
        ["retrieved", fmtTime(m.retrieval_time)],
        ["source time", fmtTime(m.source_time)],
        ["archive capture", fmtTime(m.archive_capture_time)],
        ["HTTP", m.http_status], ["media type", m.media_type],
        ["content sha256", h("span", { class: "mono" }, m.content_sha256)],
        ["custody ingestion", h("span", { class: "mono faint" }, m.custody_ingestion_id)],
        ["prior manifestation", m.prior_manifestation_id
          ? refLink("fabric_manifestation", m.prior_manifestation_id) : ""],
      ]),
      fieldAnchors.length ? [h("h2", {}, "Exact field anchors"),
        (() => {
          const bySha = {};
          for (const np of d.normalized_payloads || []) bySha[np.sha256] = np;
          const unverified = (a) => {
            const np = bySha[a.normalized_sha256];
            return !np || np.unavailable;
          };
          return table({ columns: [
            { label: "field path", render: (a) => h("span", { class: "mono" }, a.field_path) },
            { label: "value (recorded at extraction)", render: (a) =>
                h("span", { class: unverified(a) ? "mono faint" : "mono",
                            title: unverified(a)
                              ? "the payload this anchor addresses failed custody verification; value shown as recorded at extraction"
                              : "verified against the anchored payload" },
                  clip(a.exact_value, 60), unverified(a) ? " ⚠ unverified" : "") },
            { label: "observation", render: (a) => h("span", {}, a.attribute, " ",
                refLink("semantic_observation", a.observation_id, "→")) },
            { label: "mapping", render: (a) => badge(a.mapping_status) }],
            rows: fieldAnchors });
        })()] : null,
      h("h2", {}, "Native payload", d.payload ? h("span", { class: "faint" },
        ` (${d.payload.bytes ?? "?"} bytes${d.payload.truncated ? ", truncated view" : ""})`) : null),
      // span offsets are exact only within their normalized payload — the
      // native bytes render without highlights
      highlightedPayload(d.payload, []),
      (d.normalized_payloads || [])
        .filter((np) => np.sha256 !== (d.payload || {}).sha256)
        .map((np) => [
          h("h2", {}, "Normalized representation ",
            h("span", { class: "mono faint" }, np.sha256.slice(0, 12))),
          highlightedPayload(np, np.anchors || [])]),
      (d.normalized_payloads || [])
        .filter((np) => np.sha256 === (d.payload || {}).sha256)
        .map((np) => [
          h("h2", {}, "Exact anchors in payload"),
          highlightedPayload(np, np.anchors || [])]),
      h("h2", {}, `Derived observations (${d.observations.length})`),
      table({ columns: [
        { label: "attribute", key: "attribute" },
        { label: "value", render: (o) => clip(o.value, 100) },
        { label: "producer", render: (o) => badge(o.producer_kind) },
        { label: "", render: (o) => refLink("semantic_observation", o.observation_id, "inspect") }],
        rows: d.observations }),
      h("h2", {}, `Claims resting on this evidence (${d.claims.length})`),
      d.claims.map((c) => h("div", {}, refLink("semantic_claim", c.claim_id, clip(c.statement, 130)),
        " ", badge(c.epistemic_state))),
      h("h2", {}, "Analytical objects depending on it"),
      d.dependents.length ? d.dependents.map((dep) => h("div", {},
        badge(dep.kind), " ", refLink(dep.kind, dep.id, clip(dep.label, 110)), " ", badge(dep.status)))
        : emptyBox("none visible"),
      annotationsSection(d.annotations, "fabric_manifestation", id, refresh),
    ];
  });
}

// ---- sources ----------------------------------------------------------------

export async function sourcesView(main) {
  await render(main, async () => {
    const res = await get("/api/family/fabric_source_descriptor");
    return [h("h1", {}, "Sources"),
      h("p", { class: "muted" }, "the registry of where Curunír can look — capabilities, not conclusions"),
      table({
        columns: [
          { label: "source", render: (s) => refLink("fabric_source_descriptor", s.source_id,
              s.canonical_name || s.source_id) },
          { label: "type", render: (s) => [badge(s.source_type), badge(s.source_subtype)] },
          { label: "publisher", key: "publisher_name" },
          { label: "official", render: (s) => badge(s.official_status) },
          { label: "jurisdictions", render: (s) => (s.jurisdictions || []).join(", ") },
          { label: "languages", render: (s) => (s.languages || []).join(", ") },
        ],
        rows: res.records,
      })];
  });
}

export async function sourceView(main, params, id) {
  await render(main, async () => {
    const [rec, up] = await Promise.all([
      get(`/api/record/fabric_source_descriptor/${encodeURIComponent(id)}`),
      get(`/api/provenance/ascend/fabric_source_descriptor/${encodeURIComponent(id)}`),
    ]);
    const s = rec.current;
    return [
      h("h1", {}, s.canonical_name || id, " ", badge(s.source_type)),
      kv([
        ["source id", h("span", { class: "mono" }, id)],
        ["publisher", s.publisher_name], ["official status", badge(s.official_status)],
        ["jurisdictions", (s.jurisdictions || []).join(", ")],
        ["languages", (s.languages || []).join(", ")],
        ["base URLs", h("span", { class: "mono" }, (s.base_urls || []).join(" "))],
        ["independence notes", s.independence_notes],
        ["access", badge(s.access_class)],
      ]),
      h("h2", {}, "What rests on this source"),
      h("p", { class: "muted" },
        `${up.observations.length} observations → ${up.claims.length} claims → ${up.dependents.length} analytical objects`),
      table({ columns: [
        { label: "claim", render: (c) => refLink("semantic_claim", c.claim_id, clip(c.statement, 120)) },
        { label: "epistemic", render: (c) => badge(c.epistemic_state) }],
        rows: up.claims }),
      up.dependents.length ? [h("h3", {}, "Analytical dependents"),
        up.dependents.map((dep) => h("div", {}, badge(dep.kind), " ",
          refLink(dep.kind, dep.id, clip(dep.label, 110)), " ", badge(dep.status)))] : null,
    ];
  });
}

// ---- claims -----------------------------------------------------------------

export async function claimView(main, params, id) {
  const refresh = () => claimView(main, params, id);
  await render(main, async () => {
    const [descent, rec, independence] = await Promise.all([
      get(`/api/claims/${encodeURIComponent(id)}/descent`),
      get(`/api/record/semantic_claim/${encodeURIComponent(id)}`),
      get(`/api/independence?claim_ids=${encodeURIComponent(id)}`),
    ]);
    const c = rec.current;
    return [
      h("h1", {}, "Claim"),
      h("p", {}, h("b", {}, c.statement)),
      kv([
        ["subject", refLink("object", c.subject_object_id, c.subject_ref)],
        ["predicate", h("span", { class: "mono" }, c.predicate)],
        ["value / object", c.object_or_value],
        ["polarity", badge(c.polarity)],
        ["epistemic", badge(c.epistemic_state)],
        ["review", badge(c.review_state)],
        ["valid", `${fmtTime(c.valid_from)}${c.valid_to ? " → " + fmtTime(c.valid_to) : " → (open)"}`],
        ["version", c.version],
      ]),
      h("h2", {}, "Source independence"),
      h("div", { class: "statline" },
        h("span", {}, "manifestations: ", h("b", {}, independence.manifestation_count)),
        h("span", {}, "unique sources: ", h("b", {}, independence.unique_sources.length)),
        h("span", {}, "independent origins: ", h("b", {}, independence.independent_origin_count)),
        h("span", {}, "languages: ", h("b", {}, independence.languages.join(", ") || "—"))),
      independence.manifestation_count > independence.independent_origin_count
        ? h("div", { class: "notice" },
            "reach exceeds independence: multiple artifacts descend from fewer origin families")
        : null,
      h("h2", {}, "Evidence descent"),
      claimDescentView(descent),
      h("h2", {}, "Version history"),
      table({ columns: [
        { label: "v", key: "version" },
        { label: "statement", render: (r) => clip(r.statement, 110) },
        { label: "recorded", render: (r) => fmtTime(r.recorded_time) },
        { label: "epistemic", render: (r) => badge(r.epistemic_state) }],
        rows: rec.versions.slice().reverse() }),
      annotationsSection(rec.annotations, "semantic_claim", id, refresh),
    ];
  });
}

// ---- generic record inspector ------------------------------------------------

export async function recordView(main, params, kind, id) {
  const refresh = () => recordView(main, params, kind, id);
  await render(main, async () => {
    const rec = await get(`/api/record/${encodeURIComponent(kind)}/${encodeURIComponent(id)}`);
    const cur = rec.current;
    const pairs = Object.entries(cur)
      .filter(([k]) => !["record_type", "marking", "sections"].includes(k))
      .map(([k, v]) => [k, typeof v === "object" && v !== null
        ? h("span", { class: "mono faint" }, clip(JSON.stringify(v), 240)) : String(v)]);
    return [
      h("h1", {}, kind, " ", h("span", { class: "mono faint" }, clip(id, 40))),
      kv(pairs),
      rec.versions.length > 1 ? [h("h2", {}, "Versions"),
        table({ columns: [
          { label: "v", key: "version" },
          { label: "recorded", render: (r) => fmtTime(r.recorded_time) },
          { label: "status", render: (r) => badge(r.status) }],
          rows: rec.versions.slice().reverse() })] : null,
      rec.transitions.length ? [h("h2", {}, "Transitions"),
        table({ columns: [
          { label: "type", render: (t) => badge(t.transition_type) },
          { label: "detail", render: (t) => clip(t.detail, 110) },
          { label: "time", render: (t) => fmtTime(t.recorded_time) }],
          rows: rec.transitions })] : null,
      annotationsSection(rec.annotations, kind, id, refresh),
    ];
  });
}

// ---- claims: the reading surface -------------------------------------------
// What the evidence says, grouped by the thing it is about, with the VALUE as
// the headline rather than the predicate or the identifier.

function _humanPredicate(p) {
  const w = String(p || "").replace(/[_-]+/g, " ").trim().toLowerCase();
  return w.charAt(0).toUpperCase() + w.slice(1);
}

function _pips(count) {
  const n = Math.max(0, Math.min(Number(count) || 0, 3));
  return h("span", { class: "pips", title: `${count} independent source${count === 1 ? "" : "s"}` },
    [0, 1, 2].map((i) => h("i", { class: i < n ? "on" : "" })));
}

function _claimState(c) {
  const r = c.review_state, e = c.epistemic_state;
  if (r === "SUPERSEDED" || e === "SUPERSEDED") return ["superseded", "is-historic"];
  if (r === "CONTESTED" || e === "CONTESTED") return ["contested", "is-tension"];
  if (e === "INFERRED") return ["inferred", "is-open"];
  if (r === "REVIEWED" || r === "CONFIRMED") return ["reviewed", "is-settled"];
  return [String(r || e || "recorded").toLowerCase(), ""];
}

export async function claimsView(main) {
  main.replaceChildren(loading());
  try {
    const records = (await get("/api/family/semantic_claim")).records || [];
    const groups = new Map();
    for (const c of records) {
      const key = c.subject_ref || c.subject_object_id || "(unattributed)";
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(c);
    }
    const frag = h("div", {},
      h("h1", {}, "What the evidence says"),
      h("p", { class: "statline" },
        h("b", {}, String(records.length)), ` claims about `,
        h("b", {}, String(groups.size)),
        ` subject${groups.size === 1 ? "" : "s"} — select one to follow it to the captured bytes`));

    for (const [subject, group] of groups) {
      frag.append(h("h2", {}, subject));
      // A tension is the SAME subject asserting the SAME predicate with
      // different values. Anything looser invents disagreement the evidence
      // does not support.
      const byPred = new Map();
      for (const c of group) {
        const k = String(c.predicate || "").toLowerCase();
        if (!byPred.has(k)) byPred.set(k, []);
        byPred.get(k).push(c);
      }
      for (const [, g] of byPred) {
        const values = new Set(g.map((c) => String(c.object_or_value ?? "").trim()).filter(Boolean));
        if (g.length > 1 && values.size > 1) {
          frag.append(h("div", { class: "notice bad" },
            h("b", {}, "Unresolved tension"), " ",
            [...values].map((v) => `“${v}”`).join("  vs  "),
            h("div", { class: "sub" },
              "Two captures disagree; neither is retracted.")));
        }
      }
      for (const c of group) {
        const [word, cls] = _claimState(c);
        const value = String(c.object_or_value ?? "");
        frag.append(h("button", {
          class: `claim ${cls}`, type: "button",
          onclick: () => { location.hash = `#/claims/${c.claim_id}`; },
        },
          h("div", { class: "lbl" }, _humanPredicate(c.predicate)),
          h("div", { class: `val ${value.length > 48 ? "long" : ""}` }, value || "—"),
          h("div", { class: "whisper" },
            _pips(c.independent_basis_count), " ",
            h("span", { class: "state" }, word), " ",
            h("span", {}, fmtTime(c.recorded_time)), " ",
            ident(c.claim_id))));
      }
    }
    if (!groups.size) frag.append(emptyBox("no claims recorded"));
    main.replaceChildren(frag);
  } catch (err) { main.replaceChildren(errorBox(err)); }
}
