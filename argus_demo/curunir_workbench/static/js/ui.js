// DOM + shared components. Text-first epistemic labels; colour reinforces,
// never replaces, meaning.

export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "dataset") Object.assign(el.dataset, v);
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k === "html") el.innerHTML = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const child of children.flat(Infinity)) {
    if (child === undefined || child === null || child === false) continue;
    el.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return el;
}

export const text = (s) => document.createTextNode(String(s));

export function badge(value, extra = "") {
  if (value === undefined || value === null || value === "") return null;
  const cls = String(value).toLowerCase().replace(/[^a-z0-9_]+/g, "_");
  return h("span", { class: `badge ${cls} ${extra}` }, String(value));
}

export function kv(pairs) {
  const dl = h("dl", { class: "kv" });
  for (const [key, value] of pairs) {
    if (value === undefined || value === null || value === "") continue;
    dl.append(h("dt", {}, key), h("dd", {}, value.nodeType ? value : String(value)));
  }
  return dl;
}

export function table({ columns, rows, onRow, empty = "nothing visible in this context" }) {
  if (!rows.length) return h("p", { class: "empty" }, empty);
  const head = h("tr", {}, columns.map((c) => h("th", { scope: "col" }, c.label)));
  const body = rows.map((row) => {
    const tr = h("tr", { class: onRow ? "row" : "", tabindex: onRow ? "0" : undefined },
      columns.map((c) => h("td", {}, c.render ? c.render(row) : row[c.key] ?? "")));
    if (onRow) {
      tr.addEventListener("click", () => onRow(row, tr));
      tr.addEventListener("keydown", (e) => { if (e.key === "Enter") onRow(row, tr); });
    }
    return tr;
  });
  return h("table", {}, h("thead", {}, head), h("tbody", {}, body));
}

export const loading = () => h("p", { class: "loading" }, "loading…");
export const errorBox = (err) =>
  h("div", { class: "notice bad", role: "alert" },
    h("b", {}, err.status ? `error ${err.status}` : "error"), " ", err.message);
export const emptyBox = (msg) => h("p", { class: "empty" }, msg);

export function clip(s, n = 90) {
  s = String(s ?? "");
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
}

export function fmtTime(t) {
  if (!t) return "";
  return String(t).replace("T", " ").replace(/\+00:00$/, "Z").slice(0, 16);
}

// pivot link into another workbench route
export function pivot(route, label) {
  return h("a", { href: `#${route}` }, label ?? route);
}

// route for a record family id — the shared pivot map
export function recordRoute(kind, id) {
  const map = {
    object: `/entities/${id}`, activity: `/events/${id}`, event: `/events/${id}`,
    semantic_claim: `/claims/${id}`, fabric_manifestation: `/evidence/${id}`,
    fabric_source_descriptor: `/sources/${id}`, analytic_theme: `/themes/${id}`,
    analytic_narrative: `/narratives/${id}`, hypothesis: `/hypotheses/${id}`,
    analytic_forecast: `/forecasts/${id}`, forecast_indicator: `/indicators/${id}`,
    strategic_warning: `/warnings/${id}`, impact_path: `/impact/${id}`,
    mission_objective: `/impact/${id}`, stakeholder_assessment: `/stakeholders/${id}`,
    workbench_report: `/reports/${id}`, review_item: `/review`,
    collection_route: `/collection`, fabric_watch: `/watches`,
    analyst_task: `/tasks`, information_requirement: `/investigation`,
    analytic_assumption: `/record/analytic_assumption/${id}`,
    semantic_observation: `/record/semantic_observation/${id}`,
    response_option: `/record/response_option/${id}`,
  };
  return map[kind] || `/record/${kind}/${id}`;
}

// A short, stable handle. An operator refers to a record by the tail of its
// id; the whole hash is custody detail, not vocabulary.
export function handle(kind, id) {
  const family = String(kind || "").replace(/^(semantic|analytic|fabric|workbench)_/, "")
    .replace(/_/g, " ");
  const tail = String(id || "").split("-").pop().slice(-6);
  return `${family || "record"} \u00b7${tail}`;
}

// The full identifier, rendered only in Auditor mode.
export function ident(id) {
  if (id === undefined || id === null || id === "") return null;
  return h("code", { class: "ident" }, String(id));
}

export function refLink(kind, id, label) {
  if (id === "REDACTED") return h("span", { class: "faint" }, "[not accessible]");
  // A caller that knows what the record SAYS passes a label; only fall back to
  // an identifier when nothing better exists, and demote it when we do.
  const shown = label ?? handle(kind, id);
  const link = pivot(recordRoute(kind, id), shown);
  if (label !== undefined && label !== null) return link;
  return h("span", {}, link, " ", ident(id));
}

// ---- provenance chain -------------------------------------------------------

export function chainNode(node) {
  const inaccessible = node.inaccessible;
  return h("div", { class: `chain-node${inaccessible ? " inaccessible" : ""}` },
    node.layer ? h("div", { class: "layer" }, node.layer) : null,
    inaccessible
      ? h("span", {}, `${node.kind}: not accessible in this context`)
      : h("span", {}, badge(node.kind), " ", refLink(node.kind, node.id, clip(node.label, 110))));
}

export function claimDescentView(descent) {
  if (descent.inaccessible) return chainNode(descent);
  const parts = [chainNode({ ...descent.claim, layer: "PROPOSITION" }),
    h("div", { class: "faint" },
      `independent basis: ${descent.independent_basis_count} · dependence groups: ` +
      `${(descent.dependence_group_ids || []).length}`)];
  for (const obs of descent.observations) {
    if (obs.inaccessible) { parts.push(chainNode(obs)); continue; }
    parts.push(chainNode({ ...obs, layer: "EXTRACTION (source says)" }));
    for (const a of obs.anchors || []) {
      const anchor = a.anchor || {};
      const where = anchor.field_path ? `field ${anchor.field_path}`
        : (anchor.start !== null && anchor.start !== undefined)
          ? `offsets ${anchor.start}–${anchor.end}` : "document";
      parts.push(h("div", { class: "chain-node" },
        h("div", { class: "layer" }, "EXACT ANCHOR"),
        h("span", {}, `${where}`, anchor.exact_value ? h("span", { class: "mono" }, ` = “${clip(anchor.exact_value, 60)}”`) : null),
        h("div", {},
          a.manifestation && !a.manifestation.inaccessible
            ? refLink("fabric_manifestation", a.manifestation.manifestation_id, "manifestation")
            : h("span", { class: "faint" }, "[manifestation not accessible]"),
          " · ",
          a.source ? refLink("fabric_source_descriptor", a.source.source_id,
            `source ${a.source.source_id}`) : null)));
    }
  }
  return h("div", { class: "chain" }, parts);
}

// ---- SVG surfaces -----------------------------------------------------------

const SVG = "http://www.w3.org/2000/svg";
export function svg(tag, attrs = {}, ...children) {
  const el = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null) continue;
    el.setAttribute(k, v);
  }
  for (const child of children.flat(Infinity)) if (child) el.append(child);
  return el;
}

export function probabilityChart(versions, { width = 560, height = 170, onPoint } = {}) {
  // Authored versions only — the polyline connects recorded judgments; no
  // interpolation pretends to be history.
  const pad = { l: 42, r: 14, t: 12, b: 26 };
  const root = svg("svg", { viewBox: `0 0 ${width} ${height}`, class: "svg-panel",
    role: "img", "aria-label": "Authored probability history" });
  const xs = versions.map((_, i) => pad.l + (versions.length === 1 ? 0
    : (i * (width - pad.l - pad.r) / (versions.length - 1))));
  const y = (p) => pad.t + (1 - p) * (height - pad.t - pad.b);
  for (const p of [0, 0.25, 0.5, 0.75, 1]) {
    root.append(svg("line", { x1: pad.l, x2: width - pad.r, y1: y(p), y2: y(p), class: "tick" }));
    root.append(svg("text", { x: 4, y: y(p) + 4, class: "lbl-dim" }, String(p)));
  }
  if (versions.length > 1) {
    root.append(svg("polyline", {
      points: versions.map((v, i) => `${xs[i]},${y(v.probability)}`).join(" "),
      class: "probline" }));
  }
  versions.forEach((v, i) => {
    const point = svg("circle", { cx: xs[i], cy: y(v.probability), r: 4.5, class: "probpoint" },
      svg("title", {}, `v${v.version} p=${v.probability} by ${v.author}\n${v.change_reason || v.probability_basis || ""}`));
    if (onPoint) point.addEventListener("click", () => onPoint(v));
    root.append(point);
    root.append(svg("text", { x: xs[i], y: height - 8, "text-anchor": "middle", class: "lbl-dim" },
      `v${v.version}`));
  });
  return root;
}

export function graphSvg(data, { width = 860, height = 520, onNode } = {}) {
  const nodes = data.nodes;
  const root = svg("svg", { viewBox: `0 0 ${width} ${height}`, class: "svg-panel",
    role: "img", "aria-label": "Relationship graph" });
  if (!nodes.length) return root;
  const cx = width / 2, cy = height / 2, r = Math.min(cx, cy) - 70;
  const pos = {};
  nodes.forEach((n, i) => {
    const angle = (2 * Math.PI * i) / nodes.length - Math.PI / 2;
    pos[n.object_id] = [cx + r * Math.cos(angle), cy + r * Math.sin(angle)];
  });
  const edgeLabel = (e) => `${e.relation_type} [${e.basis}${e.status !== "ACTIVE" ? "/" + e.status : ""}]`;
  for (const e of data.edges) {
    const [x1, y1] = pos[e.source] || []; const [x2, y2] = pos[e.target] || [];
    if (x1 === undefined || x2 === undefined) continue;
    root.append(svg("line", { x1, y1, x2, y2,
      class: `edge${e.basis === "ASSERTED" ? " asserted" : ""}` },
      svg("title", {}, edgeLabel(e))));
    root.append(svg("text", { x: (x1 + x2) / 2, y: (y1 + y2) / 2 - 3,
      "text-anchor": "middle", class: "lbl-dim" }, e.relation_type));
  }
  for (const p of data.association_proposals || []) {
    const [x1, y1] = pos[p.source] || []; const [x2, y2] = pos[p.target] || [];
    if (x1 === undefined || x2 === undefined) continue;
    root.append(svg("line", { x1, y1, x2, y2, class: "edge proposal" },
      svg("title", {}, `identity proposal (${p.outcome})${p.open ? " — OPEN" : ""}`)));
  }
  for (const n of nodes) {
    const [x, y] = pos[n.object_id];
    const g = svg("g", { transform: `translate(${x},${y})`, tabindex: "0", role: "button" });
    g.append(svg("circle", { r: 15, class: "node" }));
    g.append(svg("text", { y: 28, "text-anchor": "middle" },
      String(n.label).slice(0, 22)));
    g.append(svg("text", { y: 3.5, "text-anchor": "middle", class: "lbl-dim" },
      n.object_type.slice(0, 3)));
    g.append(svg("title", {}, `${n.label}\n${n.object_type} [${n.epistemic_state}]` +
      (n.cluster_partially_hidden ? "\nidentity linkage partly hidden in this context" : "")));
    if (onNode) {
      g.addEventListener("click", () => onNode(n));
      g.addEventListener("keydown", (e) => { if (e.key === "Enter") onNode(n); });
    }
    root.append(g);
  }
  return root;
}

export function mapSvg(featureCollection, { width = 860, height = 460, onFeature } = {}) {
  const feats = featureCollection.features || [];
  const root = svg("svg", { viewBox: `0 0 ${width} ${height}`, class: "svg-panel",
    role: "img", "aria-label": "Evidence-backed map" });
  const points = [];
  for (const f of feats) {
    const g = f.geometry;
    if (g.type === "Point") points.push(g.coordinates.slice(0, 2));
    else for (const c of g.coordinates) points.push(c.slice(0, 2));
  }
  if (!points.length) {
    root.append(svg("text", { x: width / 2, y: height / 2, "text-anchor": "middle",
      class: "lbl-dim" }, "no evidence-backed coordinates in this projection"));
    return root;
  }
  const lons = points.map((p) => p[0]), lats = points.map((p) => p[1]);
  const minLon = Math.min(...lons), maxLon = Math.max(...lons);
  const minLat = Math.min(...lats), maxLat = Math.max(...lats);
  const pad = 60;
  const px = (lon) => pad + ((lon - minLon) / ((maxLon - minLon) || 1e-6)) * (width - 2 * pad);
  const py = (lat) => height - pad - ((lat - minLat) / ((maxLat - minLat) || 1e-6)) * (height - 2 * pad);
  for (const f of feats) {
    const props = f.properties;
    const geom = f.geometry;
    if (geom.type === "LineString") {
      root.append(svg("polyline", {
        points: geom.coordinates.map((c) => `${px(c[0])},${py(c[1])}`).join(" "),
        class: "edge" }, svg("title", {}, `${props.label} [${props.epistemic_state}]`)));
      continue;
    }
    const [x, yv] = [px(geom.coordinates[0]), py(geom.coordinates[1])];
    const node = svg("g", { transform: `translate(${x},${yv})`, tabindex: "0" });
    node.append(svg("circle", { r: 6, class: "node" }));
    node.append(svg("text", { x: 9, y: 4 }, `${props.label} [${props.epistemic_state}]`));
    node.append(svg("title", {}, `${props.label}\n${props.object_type} [${props.epistemic_state}]` +
      (props.uncertainty_m ? `\n±${props.uncertainty_m}m` : "")));
    if (onFeature) node.addEventListener("click", () => onFeature(f));
    root.append(node);
  }
  return root;
}

// ---- annotations panel ------------------------------------------------------

export function annotationList(annotations, { onResolve } = {}) {
  if (!annotations.length) return emptyBox("no annotations");
  return h("div", {}, annotations.map((a) =>
    h("div", { class: "card" },
      h("div", {}, badge(a.kind), badge(a.status), " ",
        h("b", {}, a.author), h("span", { class: "faint" }, ` · ${fmtTime(a.recorded_time)}`),
        a.reply_to ? h("span", { class: "faint" }, " · reply") : null),
      h("div", {}, a.text),
      a.resolution_note ? h("div", { class: "faint" }, `resolution: ${a.resolution_note}`) : null,
      a.status === "OPEN" && onResolve
        ? h("div", { class: "toolbar" },
            h("button", { onclick: () => onResolve(a, "RESOLVED") }, "Resolve"),
            h("button", { onclick: () => onResolve(a, "WITHDRAWN") }, "Withdraw"))
        : null)));
}
