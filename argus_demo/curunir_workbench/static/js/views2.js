// Workbench views, part 2: analysis, forecasting, operations, reports.
import { get, post } from "./api.js";
import {
  badge, chainNode, claimDescentView, clip, emptyBox, errorBox, fmtTime, h, kv,
  pivot, probabilityChart, refLink, table,
} from "./ui.js";
import { annotateBox, annotationsSection, nav, render } from "./views.js";

// ---- generic analytical family browser --------------------------------------

function familyBrowser({ title, family, idField, columns, note }) {
  return async function view(main) {
    await render(main, async () => {
      const res = await get(`/api/family/${family}`);
      return [h("h1", {}, title),
        note ? h("p", { class: "muted" }, note) : null,
        table({
          columns: [...columns,
            { label: "", render: (r) => refLink(family, r[idField], "inspect") }],
          rows: res.records,
        })];
    });
  };
}

export const themesView = familyBrowser({
  title: "Themes / issues", family: "analytic_theme", idField: "theme_id",
  note: "each theme carries its member propositions and evidence basis — inspect to see why it exists",
  columns: [
    { label: "theme", render: (t) => refLink("analytic_theme", t.theme_id, clip(t.title || t.theme_id, 80)) },
    { label: "status", render: (t) => badge(t.status) },
    { label: "authority", render: (t) => badge(t.authority) },
    { label: "claims", render: (t) => (t.member_claim_ids || []).length },
    { label: "recorded", render: (t) => fmtTime(t.recorded_time) },
  ],
});

export async function themeView(main, params, id) {
  const refresh = () => themeView(main, params, id);
  await render(main, async () => {
    const [rec, chain] = await Promise.all([
      get(`/api/record/analytic_theme/${encodeURIComponent(id)}`),
      get(`/api/provenance/descend/analytic_theme/${encodeURIComponent(id)}`).catch(() => null),
    ]);
    const t = rec.current;
    return [
      h("h1", {}, t.title || id, " ", badge(t.status), badge(t.authority)),
      h("p", {}, t.summary || ""),
      kv([
        ["member claims", (t.member_claim_ids || []).length],
        ["entities", h("span", {}, (t.entity_ids || []).map((e) =>
          h("span", {}, refLink("object", e), " ")))],
        ["lineage", (t.lineage || []).join(" → ")],
        ["recorded", fmtTime(t.recorded_time)],
      ]),
      h("h2", {}, "Why this theme exists — evidence basis"),
      chain ? chain.claims.map(claimDescentView) : emptyBox("no descent available"),
      rec.transitions.length ? [h("h2", {}, "History"),
        table({ columns: [
          { label: "transition", render: (x) => badge(x.transition_type) },
          { label: "detail", render: (x) => clip(x.detail, 120) },
          { label: "time", render: (x) => fmtTime(x.recorded_time) }],
          rows: rec.transitions })] : null,
      annotationsSection(rec.annotations, "analytic_theme", id, refresh),
    ];
  });
}

export const narrativesView = familyBrowser({
  title: "Narratives / discourse", family: "analytic_narrative", idField: "narrative_id",
  note: "propagation reach and independent corroboration are different things — inspect a narrative to see both",
  columns: [
    { label: "narrative", render: (n) => refLink("analytic_narrative", n.narrative_id,
        clip(n.title || n.core_proposition || n.narrative_id, 90)) },
    { label: "status", render: (n) => badge(n.status) },
    { label: "authority", render: (n) => badge(n.authority) },
    { label: "recorded", render: (n) => fmtTime(n.recorded_time) },
  ],
});

export async function narrativeView(main, params, id) {
  const refresh = () => narrativeView(main, params, id);
  await render(main, async () => {
    const [rec, variants, edges] = await Promise.all([
      get(`/api/record/analytic_narrative/${encodeURIComponent(id)}`),
      get("/api/family/narrative_variant"),
      get("/api/family/propagation_edge"),
    ]);
    const n = rec.current;
    const myVariants = variants.records.filter((v) => v.narrative_id === id);
    const myEdges = edges.records.filter((e) => e.narrative_id === id);
    return [
      h("h1", {}, clip(n.title || id, 90), " ", badge(n.status), badge(n.authority)),
      h("p", {}, n.core_proposition || ""),
      kv([
        ["earliest manifestation", n.earliest_manifestation_id
          ? refLink("fabric_manifestation", n.earliest_manifestation_id) : ""],
        ["origin certainty", badge(n.origin_certainty)],
        ["languages", (n.languages || []).join(", ")],
        ["independent adoption", n.independent_adoption_count],
      ]),
      n.status === "INSUFFICIENT_EVIDENCE"
        ? h("div", { class: "notice warn" },
            "INSUFFICIENT_EVIDENCE — this narrative is not an established finding")
        : null,
      h("h2", {}, `Variants (${myVariants.length})`),
      table({ columns: [
        { label: "kind", render: (v) => badge(v.variant_kind) },
        { label: "statement", render: (v) => clip(v.statement, 110) },
        { label: "first seen", render: (v) => fmtTime(v.first_seen_time) },
        { label: "manifestation", render: (v) => v.manifestation_id
            ? refLink("fabric_manifestation", v.manifestation_id, "evidence") : "" }],
        rows: myVariants }),
      h("h2", {}, `Propagation (${myEdges.length} edges)`),
      h("p", { class: "muted" }, "reach ≠ independence: derivative repetition propagates, it does not corroborate"),
      table({ columns: [
        { label: "from", render: (e) => clip(e.from_ref || e.from_manifestation_id, 40) },
        { label: "to", render: (e) => clip(e.to_ref || e.to_manifestation_id, 40) },
        { label: "kind", render: (e) => badge(e.edge_kind || e.kind) },
        { label: "basis", render: (e) => badge(e.basis || e.authority) }],
        rows: myEdges }),
      annotationsSection(rec.annotations, "analytic_narrative", id, refresh),
    ];
  });
}

// ---- stakeholders -----------------------------------------------------------

export async function stakeholdersView(main) {
  await render(main, async () => {
    const [assessments, positions, influence] = await Promise.all([
      get("/api/family/stakeholder_assessment"),
      get("/api/family/stakeholder_position").catch(() => ({ records: [] })),
      get("/api/family/influence_assertion"),
    ]);
    return [h("h1", {}, "Stakeholders / influence"),
      h("div", { class: "notice" },
        "PUBLIC POSITION ≠ INFERRED INTEREST · FORMAL AUTHORITY ≠ INFORMAL INFLUENCE · ASSOCIATION ≠ CONTROL — every row states which it is"),
      h("h2", {}, "Assessments"),
      table({ columns: [
        { label: "stakeholder", render: (s) => refLink("object", s.entity_object_id, s.entity_label || undefined) },
        { label: "context", render: (s) => clip(`${s.context_kind || ""} ${s.context_id || ""}`, 60) },
        { label: "authority", render: (s) => badge(s.authority) },
        { label: "status", render: (s) => badge(s.status) },
        { label: "", render: (s) => refLink("stakeholder_assessment", s.assessment_id, "inspect") }],
        rows: assessments.records }),
      h("h2", {}, "Influence assertions"),
      table({ columns: [
        { label: "holder", render: (i) => refLink("object", i.source_object_id) },
        { label: "kind", render: (i) => badge(i.kind) },
        { label: "over", render: (i) => refLink("object", i.target_object_id) },
        { label: "authority", render: (i) => badge(i.authority) },
        { label: "", render: (i) => refLink("record/influence_assertion", i.influence_id, "inspect") }],
        rows: influence.records })];
  });
}

export async function stakeholderView(main, params, id) {
  const refresh = () => stakeholderView(main, params, id);
  await render(main, async () => {
    const rec = await get(`/api/record/stakeholder_assessment/${encodeURIComponent(id)}`);
    const s = rec.current;
    const positionKinds = (s.positions || []);
    return [
      h("h1", {}, "Stakeholder assessment ", badge(s.status), badge(s.authority)),
      kv(Object.entries(s)
        .filter(([k, v]) => typeof v !== "object" && !["record_type"].includes(k))
        .map(([k, v]) => [k, k.includes("time") ? fmtTime(v) : String(v)])),
      positionKinds.length ? [h("h2", {}, "Positions vs interests"),
        positionKinds.map((p) => h("div", { class: "card" },
          badge(p.position_kind || p.kind), " ", clip(p.statement || "", 140),
          h("div", { class: "faint" }, p.position_kind === "PUBLIC_POSITION"
            ? "publicly stated" : "inferred — not a public statement")))] : null,
      annotationsSection(rec.annotations, "stakeholder_assessment", id, refresh),
    ];
  });
}

// ---- impact -----------------------------------------------------------------

export async function impactView(main) {
  await render(main, async () => {
    const [objectives, paths, assumptions, options] = await Promise.all([
      get("/api/family/mission_objective"), get("/api/family/impact_path"),
      get("/api/family/analytic_assumption"), get("/api/family/response_option"),
    ]);
    return [h("h1", {}, "Impact / exposure"),
      h("h2", {}, "Mission objectives"),
      table({ columns: [
        { label: "objective", render: (o) => refLink("mission_objective", o.objective_id, clip(o.statement, 100)) },
        { label: "priority", render: (o) => badge(o.priority) },
        { label: "status", render: (o) => badge(o.status) }],
        rows: objectives.records }),
      h("h2", {}, "Impact paths"),
      table({ columns: [
        { label: "path", render: (p) => refLink("impact_path", p.path_id, clip(p.summary, 100)) },
        { label: "objective", render: (p) => refLink("mission_objective", p.objective_id, "objective") },
        { label: "authority", render: (p) => badge(p.authority) },
        { label: "status", render: (p) => badge(p.status) }],
        rows: paths.records }),
      h("h2", {}, "Assumptions"),
      table({ columns: [
        { label: "assumption", render: (a) => refLink("analytic_assumption", a.assumption_id, clip(a.statement, 110)) },
        { label: "status", render: (a) => badge(a.status) }],
        rows: assumptions.records }),
      options.records.length ? [h("h2", {}, "Response options (proposals, never executions)"),
        table({ columns: [
          { label: "option", render: (o) => refLink("response_option", o.option_id, clip(o.description, 110)) },
          { label: "status", render: (o) => badge(o.status) }],
          rows: options.records })] : null];
  });
}

export async function impactPathView(main, params, id) {
  const refresh = () => impactPathView(main, params, id);
  await render(main, async () => {
    let rec;
    try { rec = await get(`/api/record/impact_path/${encodeURIComponent(id)}`); }
    catch { return objectiveView(main, params, id); }
    const p = rec.current;
    return [
      h("h1", {}, "Impact path ", badge(p.status), badge(p.authority)),
      h("p", {}, p.summary),
      h("h2", {}, "Chain — each link states its own basis"),
      h("div", { class: "chain" }, (p.edges || []).map((e) =>
        h("div", { class: "chain-node" },
          h("div", { class: "layer" }, `${e.edge_kind} · ${e.effect_order} · ${e.authority}`),
          h("div", {}, refLink("object", e.from_id, clip(e.from_id, 40)), " → ",
            e.to_kind === "mission_objective"
              ? refLink("mission_objective", e.to_id, "OBJECTIVE") : refLink("object", e.to_id, clip(e.to_id, 40))),
          e.note ? h("div", { class: "faint" }, `reasoning: ${e.note}`) : null,
          (e.basis_ids || []).length ? h("div", { class: "faint" }, "evidence: ",
            e.basis_ids.map((b) => h("span", {}, refLink("semantic_claim", b, clip(b, 24)), " "))) : null,
          (e.assumption_ids || []).length ? h("div", { class: "faint" }, "assumptions: ",
            e.assumption_ids.map((a) => h("span", {}, refLink("analytic_assumption", a, clip(a, 24)), " "))) : null,
          e.edge_kind === "INFERENCE" ? h("div", {}, badge("INFERRED — not observed")) : null))),
      annotationsSection(rec.annotations, "impact_path", id, refresh),
    ];
  });
}

async function objectiveView(main, params, id) {
  const refresh = () => objectiveView(main, params, id);
  await render(main, async () => {
    const [rec, paths, warnings] = await Promise.all([
      get(`/api/record/mission_objective/${encodeURIComponent(id)}`),
      get("/api/family/impact_path"), get("/api/family/strategic_warning"),
    ]);
    const o = rec.current;
    return [
      h("h1", {}, "Objective ", badge(o.priority), badge(o.status)),
      h("p", {}, h("b", {}, o.statement)),
      h("h2", {}, "Impact paths into this objective"),
      paths.records.filter((p) => p.objective_id === id).map((p) =>
        h("div", { class: "card" }, refLink("impact_path", p.path_id, clip(p.summary, 120)),
          " ", badge(p.authority))),
      h("h2", {}, "Warnings on this objective"),
      warnings.records.filter((w) => w.objective_id === id).map((w) =>
        h("div", {}, badge(w.tier, "tier"), " ", refLink("strategic_warning", w.warning_id, w.warning_id))),
      annotationsSection(rec.annotations, "mission_objective", id, refresh),
    ];
  });
}

// ---- hypotheses -------------------------------------------------------------

export async function hypothesesView(main) {
  await render(main, async () => {
    const matrix = await get("/api/hypotheses/matrix");
    const hypotheses = matrix.hypotheses;
    return [
      h("h1", {}, "Hypotheses"),
      table({ columns: [
        { label: "hypothesis", render: (hy) => refLink("hypothesis", hy.hypothesis_id, clip(hy.statement, 110)) },
        { label: "status", render: (hy) => badge(hy.status) },
        { label: "support", key: "supporting_count" },
        { label: "contradict", key: "contradicting_count" },
        { label: "assumptions", render: (hy) => (hy.assumptions || []).length },
        { label: "unknowns", render: (hy) => (hy.unknowns || []).length }],
        rows: hypotheses }),
      hypotheses.length >= 1 ? [
        h("h2", {}, "Evidence matrix — projected from actual claim links"),
        h("table", { class: "matrix" },
          h("thead", {}, h("tr", {}, h("th", {}, "evidence (claims)"),
            h("th", {}, "independence"),
            hypotheses.map((hy) => h("th", {}, clip(hy.statement, 40))))),
          h("tbody", {}, matrix.rows.map((row) => h("tr", {},
            h("td", {}, refLink("semantic_claim", row.claim_id, clip(row.statement, 70)),
              row.state !== "ACTIVE" ? [" ", badge(row.state)] : null),
            h("td", {}, `${row.independent_basis_count} origin(s)`),
            hypotheses.map((hy) => {
              const cell = row.cells[hy.hypothesis_id];
              const sym = { SUPPORTS: "+", CONTRADICTS: "−", UNRESOLVED: "?" }[cell] || "?";
              return h("td", { class: `cell-${cell.toLowerCase()}`, title: cell }, sym);
            }))))),
        h("p", { class: "faint" }, "+ supports · − contradicts · ? unresolved — click a claim to inspect its exact evidence")] : null,
      matrix.discriminators.length ? [h("h2", {}, "Discriminating observations"),
        table({ columns: [
          { label: "question", render: (d) => clip(d.question, 100) },
          { label: "status", render: (d) => badge(d.status) },
          { label: "", render: (d) => refLink("record/discriminator", d.discriminator_id, "inspect") }],
          rows: matrix.discriminators })] : null,
    ];
  });
}

export async function hypothesisView(main, params, id) {
  const refresh = () => hypothesisView(main, params, id);
  await render(main, async () => {
    const [rec, chain] = await Promise.all([
      get(`/api/record/hypothesis/${encodeURIComponent(id)}`),
      get(`/api/provenance/descend/hypothesis/${encodeURIComponent(id)}`).catch(() => null),
    ]);
    const hy = rec.current;
    const statusSelect = h("select", {},
      ["OPEN", "SUPPORTED", "WEAKLY_SUPPORTED", "DISPUTED", "REJECTED", "UNRESOLVED"]
        .map((s) => h("option", { selected: s === hy.status || undefined }, s)));
    const rationale = h("textarea", { placeholder: "assessment rationale (required)…" });
    const assessStatus = h("span", {});
    return [
      h("h1", {}, "Hypothesis ", badge(hy.status), badge(hy.review_state)),
      h("p", {}, h("b", {}, hy.statement)),
      kv([
        ["case", hy.case_id],
        ["independent evidence", hy.independent_evidence_count],
        ["source dependence", hy.source_dependence_summary],
        ["assumptions", (hy.assumptions || []).join("; ")],
        ["unknowns", (hy.unknowns || []).join("; ")],
      ]),
      h("h2", {}, "Assessment history"),
      h("div", { class: "chain" }, (hy.history || []).map((entry) =>
        h("div", { class: "chain-node mono" }, clip(entry, 160)))),
      h("h2", {}, "Evidence"),
      chain ? chain.claims.map(claimDescentView) : emptyBox("none linked"),
      h("h2", {}, "Record an assessment (attributable, versioned)"),
      h("div", { class: "card" },
        h("label", {}, "status"), statusSelect,
        h("label", {}, "rationale"), rationale,
        h("div", { class: "toolbar" },
          h("button", { class: "primary", onclick: async () => {
            try {
              await post(`/api/commands/hypotheses/${id}/assess`, {
                expected_version: hy.version, status: statusSelect.value,
                rationale: rationale.value });
              refresh();
            } catch (err) { assessStatus.replaceChildren(errorBox(err)); }
          } }, "Record assessment"), assessStatus),
        h("p", { class: "faint" },
          "disagreement with another analyst? record DISSENT below — assessments never silently overwrite")),
      h("div", { class: "card" },
        h("h3", {}, "Link a claim"),
        (() => {
          const claimInput = h("input", { placeholder: "claim id…", size: 34 });
          const stance = h("select", {}, ["supporting", "contradicting", "unresolved"]
            .map((s) => h("option", {}, s)));
          const why = h("input", { placeholder: "rationale…", size: 34 });
          const linkStatus = h("span", {});
          return h("div", { class: "toolbar" }, claimInput, stance, why,
            h("button", { onclick: async () => {
              try {
                await post(`/api/commands/hypotheses/${id}/link`, {
                  claim_id: claimInput.value, stance: stance.value, rationale: why.value });
                refresh();
              } catch (err) { linkStatus.replaceChildren(errorBox(err)); }
            } }, "Link"), linkStatus);
        })()),
      annotationsSection(rec.annotations, "hypothesis", id, refresh),
    ];
  });
}

// ---- forecasts --------------------------------------------------------------

export async function forecastsView(main) {
  await render(main, async () => {
    const res = await get("/api/family/analytic_forecast");
    return [h("h1", {}, "Forecasts"),
      h("p", { class: "muted" }, "probabilities are authored judgments — there is no recalculate button"),
      table({ columns: [
        { label: "p", render: (f) => h("b", { class: "mono" }, Number(f.probability).toFixed(2)) },
        { label: "question", render: (f) => refLink("analytic_forecast", f.forecast_id, clip(f.question, 110)) },
        { label: "status", render: (f) => badge(f.status) },
        { label: "author", key: "author" },
        { label: "horizon", render: (f) => fmtTime(f.horizon_time) },
        { label: "v", key: "version" }],
        rows: res.records })];
  });
}

export async function forecastView(main, params, id) {
  const refresh = () => forecastView(main, params, id);
  await render(main, async () => {
    const [rec, chain] = await Promise.all([
      get(`/api/record/analytic_forecast/${encodeURIComponent(id)}`),
      get(`/api/provenance/descend/analytic_forecast/${encodeURIComponent(id)}`).catch(() => null),
    ]);
    const f = rec.current;
    const open = f.status === "OPEN";
    const prob = h("input", { type: "number", min: "0.01", max: "0.99", step: "0.01",
      value: String(f.probability) });
    const basisInput = h("input", { placeholder: "authored basis for THIS number…", size: 44 });
    const reasonInput = h("input", { placeholder: "why it moved…", size: 34 });
    const moveStatus = h("span", {});
    return [
      h("h1", {}, "Forecast ", badge(f.status), badge(f.authority)),
      h("p", {}, h("b", {}, f.question)),
      kv([
        ["current probability", h("b", { class: "mono" }, Number(f.probability).toFixed(2))],
        ["authored by", `${f.author} (${f.provenance_kind})`],
        ["probability basis", f.probability_basis],
        ["outcome semantics", f.outcome_semantics],
        ["horizon", fmtTime(f.horizon_time)],
        ["domain", f.domain],
        ["resolution rule", h("span", {}, badge(f.resolution?.kind), " ", f.resolution?.criteria)],
        ["absence coverage", f.resolution?.absence_required_source_ids?.length
          ? `requires ${f.resolution.absence_min_successful_sources} of [${f.resolution.absence_required_source_ids.join(", ")}]` : ""],
        ["outcome", f.outcome ? [badge(f.outcome), ` resolved ${fmtTime(f.resolved_time)} by ${f.resolver_id} (${f.resolver_kind})`] : "unresolved"],
      ]),
      h("h2", {}, "Authored probability history — every point is a recorded judgment"),
      probabilityChart(rec.versions),
      table({ columns: [
        { label: "v", key: "version" },
        { label: "p", render: (v) => h("b", { class: "mono" }, Number(v.probability).toFixed(2)) },
        { label: "author", key: "author" },
        { label: "reason", render: (v) => clip(v.change_reason || v.probability_basis, 90) },
        { label: "recorded", render: (v) => fmtTime(v.recorded_time) }],
        rows: rec.versions.slice().reverse() }),
      (f.assumption_ids || []).length ? [h("h2", {}, "Assumptions"),
        f.assumption_ids.map((a) => h("div", {}, refLink("analytic_assumption", a)))] : null,
      (f.indicator_ids || []).length ? [h("h2", {}, "Indicators"),
        f.indicator_ids.map((i) => h("div", {}, refLink("forecast_indicator", i)))] : null,
      h("h2", {}, "Evidence basis"),
      chain ? chain.claims.map(claimDescentView) : emptyBox("no claims in basis"),
      open ? h("div", { class: "card" },
        h("h3", {}, "Move probability (authored human act)"),
        h("div", { class: "toolbar" }, prob, basisInput, reasonInput,
          h("button", { class: "primary", onclick: async () => {
            try {
              await post(`/api/commands/forecasts/${id}/move`, {
                expected_version: f.version, probability: Number(prob.value),
                probability_basis: basisInput.value, change_reason: reasonInput.value });
              refresh();
            } catch (err) { moveStatus.replaceChildren(errorBox(err)); }
          } }, "Record movement"), moveStatus),
        h("p", { class: "faint" },
          "a stale version is refused (409) — reload to rebase on the current judgment")) : null,
      open ? h("div", { class: "card" },
        h("h3", {}, "Project warning onto an objective (tier comes from the named rule)"),
        (() => {
          const objectiveInput = h("input", { placeholder: "objective id…", size: 34 });
          const projStatus = h("span", {});
          return h("div", { class: "toolbar" }, objectiveInput,
            h("button", { onclick: async () => {
              try {
                const w = await post(`/api/commands/forecasts/${id}/project-warning`,
                  { objective_id: objectiveInput.value });
                nav(`/warnings/${w.warning_id}`);
              } catch (err) { projStatus.replaceChildren(errorBox(err)); }
            } }, "Project"), projStatus);
        })()) : null,
      annotationsSection(rec.annotations, "analytic_forecast", id, refresh),
    ];
  });
}

// ---- indicators -------------------------------------------------------------

export async function indicatorsView(main) {
  await render(main, async () => {
    const res = await get("/api/family/forecast_indicator");
    return [h("h1", {}, "Indicators"),
      h("p", { class: "muted" },
        "“not observed” and “validly absent under declared coverage” are different states"),
      table({ columns: [
        { label: "indicator", render: (i) => refLink("forecast_indicator", i.indicator_id,
            clip(i.description || i.indicator_id, 90)) },
        { label: "type", render: (i) => badge(i.indicator_type || i.kind) },
        { label: "status", render: (i) => badge(i.status) },
        { label: "deadline", render: (i) => fmtTime(i.deadline_time || i.deadline) },
        { label: "forecast", render: (i) => i.forecast_id ? refLink("analytic_forecast", i.forecast_id, "→") : "" }],
        rows: res.records })];
  });
}

// ---- warnings ---------------------------------------------------------------

export async function warningsView(main) {
  await render(main, async () => {
    const res = await get("/api/family/strategic_warning");
    return [h("h1", {}, "Warning center"),
      table({ columns: [
        { label: "tier", render: (w) => badge(w.tier, "tier") },
        { label: "context", render: (w) => refLink("strategic_warning", w.warning_id,
            clip(w.mission_context || w.warning_id, 80)) },
        { label: "band", render: (w) => badge(w.probability_band) },
        { label: "consequence", render: (w) => badge(w.consequence) },
        { label: "time pressure", render: (w) => badge(w.time_pressure) },
        { label: "confidence", render: (w) => badge(w.evidence_confidence) },
        { label: "status", render: (w) => badge(w.status) }],
        rows: res.records })];
  });
}

export async function warningView(main, params, id) {
  const refresh = () => warningView(main, params, id);
  await render(main, async () => {
    const [rec, chain] = await Promise.all([
      get(`/api/record/strategic_warning/${encodeURIComponent(id)}`),
      get(`/api/provenance/descend/strategic_warning/${encodeURIComponent(id)}`),
    ]);
    const w = rec.current;
    return [
      h("h1", {}, "Warning ", badge(w.tier, "tier"), badge(w.status)),
      h("p", { class: "muted" }, w.mission_context),
      h("h2", {}, "Why this warning exists"),
      h("div", { class: "card" },
        h("p", {}, `Tier ${w.tier} is produced by named rule `,
          h("span", { class: "mono" }, w.tier_rule_id), " from:"),
        kv([
          ["probability band", badge(w.probability_band)],
          ["consequence", badge(w.consequence)],
          ["time pressure", badge(w.time_pressure)],
          ["evidence confidence", badge(w.evidence_confidence)],
        ]),
        h("h3", {}, "Component basis"),
        h("div", {}, (w.component_basis || []).map(([component, why]) =>
          h("div", { class: "chain-node" },
            h("div", { class: "layer" }, component), h("div", {}, why))))),
      h("h2", {}, "Provenance descent — warning → forecast → impact → assumptions → evidence"),
      h("div", { class: "chain" }, chain.chain.map(chainNode)),
      chain.claims.map(claimDescentView),
      h("h2", {}, "History"),
      table({ columns: [
        { label: "v", key: "version" },
        { label: "tier", render: (v) => badge(v.tier, "tier") },
        { label: "reason", render: (v) => clip(v.change_reason || "initial", 100) },
        { label: "recorded", render: (v) => fmtTime(v.recorded_time) }],
        rows: rec.versions.slice().reverse() }),
      annotationsSection(rec.annotations, "strategic_warning", id, refresh),
    ];
  });
}

// ---- collection -------------------------------------------------------------

export async function collectionView(main) {
  const refresh = () => collectionView(main);
  await render(main, async () => {
    const [needs, routes, coverage, requirements] = await Promise.all([
      get("/api/family/fabric_information_need"),
      get("/api/family/collection_route"),
      get("/api/coverage"),
      get("/api/family/information_requirement").catch(() => ({ records: [] })),
    ]);
    const launchStatus = h("div", {});
    const launch = async (route) => {
      launchStatus.replaceChildren(h("p", { class: "loading" },
        `launching route via ${route.source_id}… (live acquisition)`));
      try {
        const result = await post(`/api/commands/routes/${route.route_id}/launch`);
        launchStatus.replaceChildren(h("div", { class: "notice" },
          `route executed: ${clip(JSON.stringify(result), 300)}`));
        refresh();
      } catch (err) { launchStatus.replaceChildren(errorBox(err)); }
    };
    const assign = async (route) => {
      const actor = prompt("assign to analyst id:");
      if (!actor) return;
      try { await post(`/api/commands/routes/${route.route_id}/assign`, { assigned_actor: actor }); refresh(); }
      catch (err) { launchStatus.replaceChildren(errorBox(err)); }
    };
    return [
      h("h1", {}, "Collection"),
      launchStatus,
      h("h2", {}, "EIV-ranked routes — ranking factors are inspectable"),
      routes.records.length ? routes.records
        .sort((a, b) => (a.rank ?? 99) - (b.rank ?? 99))
        .map((r) => h("div", { class: "card" },
          h("div", {}, h("b", {}, `#${r.rank} `), badge(r.status),
            badge(r.automatable ? "AUTOMATABLE" : "HUMAN_REQUIRED"),
            ` score ${Number(r.score).toFixed(3)} · ${r.source_id} ${r.operation} `,
            h("span", { class: "mono" }, clip(r.query_value, 50))),
          h("div", { class: "muted" }, r.explanation),
          h("div", { class: "faint" }, "factors: ",
            (r.factors || []).map(([name, v]) => `${name}=${Number(v).toFixed(2)}`).join(" · ")),
          r.human_reason ? h("div", { class: "faint" }, `human required: ${r.human_reason}`) : null,
          h("div", { class: "toolbar" },
            r.status === "PROPOSED" && r.automatable
              ? h("button", { class: "primary", onclick: () => launch(r) }, "Launch route") : null,
            r.status === "PROPOSED" && !r.automatable
              ? h("button", { onclick: () => assign(r) }, "Assign to analyst") : null,
            r.execution_id ? refLink("record/fabric_execution", r.execution_id, "execution") : null,
            r.task_id ? pivot("/tasks", "task") : null)))
        : emptyBox("no proposed routes — open information requirements generate them"),
      h("h2", {}, "Information requirements"),
      table({ columns: [
        { label: "question", render: (r) => clip(r.question, 100) },
        { label: "priority", render: (r) => badge(r.priority) },
        { label: "status", render: (r) => badge(r.status) }],
        rows: requirements.records }),
      h("h2", {}, "Coverage matrix — NOT_SEARCHED is a state, not absence"),
      coverage.rows.length ? coverage.rows.map((row) =>
        h("div", { class: "card" },
          h("div", {}, h("b", {}, clip(row.question || row.need_id, 110))),
          table({ columns: [
            { label: "source family", render: ([fam]) => fam },
            { label: "state", render: ([, cell]) => badge(cell.state) },
            { label: "gaps", render: ([, cell]) => h("span", { class: "faint" }, (cell.gaps || []).join("; ")) },
            { label: "assessed", render: ([, cell]) => fmtTime(cell.assessed_time) }],
            rows: Object.entries(row.families) })))
        : emptyBox("no coverage assessments recorded"),
      h("h2", {}, "Information needs (fabric)"),
      table({ columns: [
        { label: "question", render: (n) => clip(n.question, 100) },
        { label: "mission", key: "mission_context" },
        { label: "requirement", render: (n) => n.requirement_id ? clip(n.requirement_id, 28) : "" }],
        rows: needs.records }),
    ];
  });
}

// ---- watches ----------------------------------------------------------------

export async function watchesView(main) {
  const refresh = () => watchesView(main);
  await render(main, async () => {
    const [watches, runs] = await Promise.all([
      get("/api/family/fabric_watch"), get("/api/family/fabric_watch_run")]);
    const lastRun = {};
    for (const r of runs.records) lastRun[r.watch_id] = r;
    const status = h("div", {});
    const toggle = async (w) => {
      try {
        await post(`/api/commands/watches/${w.watch_id}/active`,
          { active: !w.active, expected_active: !!w.active });
        refresh();
      } catch (err) { status.replaceChildren(errorBox(err)); }
    };
    return [h("h1", {}, "Watches"), status,
      table({ columns: [
        { label: "target", render: (w) => h("span", { class: "mono" }, clip(w.target_ref, 46)) },
        { label: "source / op", render: (w) => `${w.source_id} ${w.operation}` },
        { label: "cadence", render: (w) => `${w.cadence_seconds}s` },
        { label: "state", render: (w) => badge(w.active ? "ACTIVE" : "PAUSED") },
        { label: "last run", render: (w) => {
            const r = lastRun[w.watch_id];
            return r ? [fmtTime(r.started_time), " ", badge(r.outcome)] : h("span", { class: "faint" }, "never"); } },
        { label: "blind spots", render: (w) => h("span", { class: "faint" }, (w.blind_spots || []).join("; ")) },
        { label: "", render: (w) => h("button", { onclick: () => toggle(w) }, w.active ? "Pause" : "Resume") }],
        rows: watches.records }),
      h("p", { class: "faint" }, "runs execute through the fabric scheduler; the workbench only sets desired state")];
  });
}

// ---- tasks ------------------------------------------------------------------

export async function tasksView(main) {
  const refresh = () => tasksView(main);
  await render(main, async () => {
    const ov = await get("/api/overview");
    const all = await get("/api/family/analytic_transition").catch(() => null);
    const session = await get("/api/session");
    const tasks = ov.open_tasks;
    const doneTasks = [];
    const status = h("div", {});
    const move = async (t, to) => {
      const note = ["DONE", "FAILED", "ABANDONED"].includes(to) ? (prompt(`${to} note:`) || "") : "";
      try {
        await post("/api/commands/workflow/transition", {
          subject_kind: "analyst_task", subject_id: t.task_id, to_status: to, note });
        refresh();
      } catch (err) { status.replaceChildren(errorBox(err)); }
    };
    return [h("h1", {}, "Tasks"), status,
      tasks.length ? tasks.map((t) => h("div", { class: "card" },
        h("div", {}, badge(t.status), t.overdue ? badge("OVERDUE") : null,
          badge(t.task_type), " ", h("b", {}, clip(t.required_action, 120))),
        h("div", { class: "faint" },
          `assigned to ${t.assigned_actor} (${t.assigned_role}) · created ${fmtTime(t.created_time)}` +
          (t.due_time ? ` · due ${fmtTime(t.due_time)}` : "")),
        (t.affected_ids || []).length ? h("div", { class: "faint" }, "affects: ",
          t.affected_ids.map((aid) => h("span", {}, clip(aid, 30), " "))) : null,
        h("div", { class: "toolbar" },
          t.status === "ASSIGNED" ? h("button", { onclick: () => move(t, "IN_PROGRESS") }, "Start") : null,
          ["ASSIGNED", "IN_PROGRESS"].includes(t.status) && t.assigned_actor === session.actor_id
            ? h("button", { class: "primary", onclick: () => move(t, "DONE") }, "Complete") : null,
          t.status === "IN_PROGRESS" ? h("button", { onclick: () => move(t, "BLOCKED") }, "Blocked") : null,
          h("span", { class: "faint" },
            t.assigned_actor !== session.actor_id ? "completion is reserved for the assigned analyst" : ""))))
        : emptyBox("no open tasks"),
      h("h2", {}, "Transitions are attributable"),
      h("p", { class: "faint" }, "every state change above is a recorded workflow transition with your actor identity — see ",
        pivot("/activity", "activity"))];
  });
}

// ---- review -----------------------------------------------------------------

export async function reviewView(main) {
  const refresh = () => reviewView(main);
  await render(main, async () => {
    const queue = await get("/api/review");
    const status = h("div", {});
    const dispose = async (item, s) => {
      const note = prompt(`${s} note (kept in history):`) || "";
      if (!note) return;
      try {
        await post(`/api/commands/review/${item.id}/resolve`, {
          expected_version: item.version, status: s, note });
        refresh();
      } catch (err) { status.replaceChildren(errorBox(err)); }
    };
    const proposal = async (item, accept) => {
      const note = prompt(`${accept ? "accept" : "reject"} note:`) || "";
      try {
        await post(`/api/commands/proposals/${item.id}/resolve`, { accept, note });
        refresh();
      } catch (err) { status.replaceChildren(errorBox(err)); }
    };
    return [h("h1", {}, `Review queue (${queue.open_count} open)`), status,
      queue.items.length ? queue.items.map((item) => h("div", { class: "card" },
        h("div", {}, badge(item.queue), badge(item.kind), badge(item.status)),
        h("div", {}, clip(item.detail, 200)),
        h("div", { class: "faint" },
          item.subject_kind && item.subject_id
            ? ["subject: ", refLink(item.subject_kind, item.subject_id, clip(item.subject_id, 40)), " · "]
            : null,
          fmtTime(item.recorded_time)),
        (item.evidence_refs || []).length ? h("div", { class: "faint" }, "evidence: ",
          item.evidence_refs.map((e) => h("span", {}, clip(e, 28), " "))) : null,
        item.resolution_note ? h("div", { class: "faint" }, `resolution: ${item.resolution_note}`) : null,
        item.status === "OPEN" && item.queue === "SEMANTIC" ? h("div", { class: "toolbar" },
          h("button", { class: "primary", onclick: () => dispose(item, "RESOLVED") }, "Resolve"),
          h("button", { onclick: () => dispose(item, "DISMISSED") }, "Dismiss (kept in history)")) : null,
        item.status === "OPEN" && item.queue === "MODEL_PROPOSAL" ? h("div", { class: "toolbar" },
          h("button", { class: "primary", onclick: () => proposal(item, true) }, "Accept (human act)"),
          h("button", { onclick: () => proposal(item, false) }, "Reject")) : null,
        item.queue === "REPORT" ? h("div", { class: "toolbar" },
          pivot(`/reports/${item.id}`, "open report for review")) : null))
        : emptyBox("review queue clear")];
  });
}

// ---- annotations (global) ---------------------------------------------------

export async function annotationsView(main) {
  await render(main, async () => {
    const res = await get("/api/family/workbench_annotation");
    return [h("h1", {}, "Annotations / collaboration"),
      table({ columns: [
        { label: "kind", render: (a) => badge(a.kind) },
        { label: "author", key: "author" },
        { label: "text", render: (a) => clip(a.text, 100) },
        { label: "target", render: (a) => refLink(a.target_kind, a.target_id, clip(a.target_id, 34)) },
        { label: "status", render: (a) => badge(a.status) },
        { label: "time", render: (a) => fmtTime(a.recorded_time) }],
        rows: res.records.slice().reverse() })];
  });
}

// ---- investigation ----------------------------------------------------------

export async function investigationView(main) {
  const refresh = () => investigationView(main);
  await render(main, async () => {
    const ov = await get("/api/overview");
    const hyp = await get("/api/family/hypothesis");
    const question = h("input", { placeholder: "information requirement question…", size: 50 });
    const rationale = h("input", { placeholder: "why it matters…", size: 36 });
    const priority = h("select", {}, ["LOW", "MEDIUM", "HIGH", "CRITICAL"].map((p) =>
      h("option", { selected: p === "MEDIUM" || undefined }, p)));
    const status = h("div", {});
    const hypStatement = h("input", { placeholder: "hypothesis statement…", size: 60 });
    const hypCase = h("input", { placeholder: "case id…", size: 20 });
    return [
      h("h1", {}, "Investigation"),
      h("p", { class: "muted" },
        "question → evidence → uncertainty → collection → update → assessment → output"),
      status,
      h("h2", {}, "Objectives"),
      ov.objectives.map((o) => h("div", {}, badge(o.priority),
        refLink("mission_objective", o.objective_id, clip(o.statement, 110)))),
      h("h2", {}, "Open information requirements"),
      table({ columns: [
        { label: "question", render: (r) => clip(r.question, 100) },
        { label: "priority", render: (r) => badge(r.priority) },
        { label: "status", render: (r) => badge(r.status) },
        { label: "rationale", render: (r) => h("span", { class: "faint" }, clip(r.rationale, 60)) }],
        rows: ov.open_requirements }),
      h("div", { class: "card" },
        h("h3", {}, "Open a requirement"),
        h("div", { class: "toolbar" }, question, priority, rationale,
          h("button", { class: "primary", onclick: async () => {
            try {
              await post("/api/commands/requirements", {
                question: question.value, priority: priority.value,
                mission_context: "workbench", rationale: rationale.value });
              refresh();
            } catch (err) { status.replaceChildren(errorBox(err)); }
          } }, "Open"))),
      h("h2", {}, "Hypotheses"),
      hyp.records.map((hy) => h("div", {}, badge(hy.status),
        refLink("hypothesis", hy.hypothesis_id, clip(hy.statement, 110)))),
      h("div", { class: "card" },
        h("h3", {}, "Record a hypothesis (starts UNRESOLVED — never born supported)"),
        h("div", { class: "toolbar" }, hypStatement, hypCase,
          h("button", { class: "primary", onclick: async () => {
            try {
              await post("/api/commands/hypotheses", {
                statement: hypStatement.value, case_id: hypCase.value || "workbench" });
              refresh();
            } catch (err) { status.replaceChildren(errorBox(err)); }
          } }, "Record"))),
      h("h2", {}, "Collection & tasks"),
      h("p", {}, pivot("/collection", `pending routes: ${ov.pending_routes.length}`), " · ",
        pivot("/tasks", `open tasks: ${ov.open_tasks.length}`), " · ",
        pivot("/review", `review items: ${ov.open_review_items.length}`)),
      h("h2", {}, "Outputs"),
      h("p", {}, pivot("/reports", `reports: ${ov.counts.reports}`)),
      await (async () => {
        const saved = await get("/api/family/workbench_saved_view").catch(() => ({ records: [] }));
        if (!saved.records.length) return null;
        return [h("h2", {}, "Saved investigation views"),
          saved.records.map((v) => {
            const d = v.definition || {};
            const target = v.view_kind === "graph"
              ? `/graph?focus=${encodeURIComponent(d.focus || "")}&depth=${d.depth || 2}`
              : v.view_kind === "timeline"
                ? `/timeline?axis=${d.axis || "knowledge"}&kinds=${d.kinds || ""}`
                : `/${v.view_kind}`;
            return h("div", {}, pivot(target, v.title),
              h("span", { class: "faint" }, ` — ${v.view_kind} · by ${v.author}`));
          })];
      })(),
    ];
  });
}
