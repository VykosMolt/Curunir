"""Operational workshops and the common operating picture.

A workshop definition is validated data: object types, tables, map layers,
timeline sources, allowed actions and access requirements. The renderer works
only from an access-filtered projection, and the picture is a self-contained
HTML document with an inline SVG map and no external assets. Epistemic state
always shows as symbol shape and text, never colour alone.
"""
from __future__ import annotations

import html
from typing import Any, Mapping

from .access import AccessContext, ROLE_RANK
from .contracts import ANALYST_ACTION_KINDS, OBJECT_TYPES, RELATION_TYPES
from .canonical import sha256
from .projection import Projection
from .schema_registry import SchemaError

EPISTEMIC_SYMBOLS = {"OBSERVED": "circle", "REPORTED": "square", "EXTRACTED": "square", "INFERRED": "triangle",
                     "PREDICTED": "diamond", "PLANNED": "diamond", "DISPUTED": "cross", "CANCELLED": "cross",
                     "CORRECTED": "square", "UNKNOWN": "question"}
FILTER_OPS = ("eq", "in")
TIMELINE_KINDS = ("object_version", "activity", "alert", "recommendation", "decision", "analyst_action",
                  "association_proposal", "ingestion")


class WorkbenchAccessError(PermissionError):
    pass


def validate_workshop_definition(definition: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {"record_type", "workshop_id", "version", "purpose", "object_types", "relationship_types",
               "filters", "tables", "map_layers", "timeline", "alert_rules", "actions", "access",
               "reports", "workflow", "relationship_tables", "definition_sha256"}
    unknown = set(definition) - allowed
    if unknown:
        raise SchemaError(f"unknown workshop keys: {sorted(unknown)}")
    for key in ("workshop_id", "version", "purpose", "object_types", "tables", "access"):
        if key not in definition:
            raise SchemaError(f"workshop missing {key}")
    for object_type in definition["object_types"]:
        if object_type not in OBJECT_TYPES:
            raise SchemaError(f"unknown object type in workshop: {object_type}")
    for relation_type in definition.get("relationship_types", []):
        if relation_type not in RELATION_TYPES:
            raise SchemaError(f"unknown relationship type in workshop: {relation_type}")
    for filter_rule in definition.get("filters", []):
        extra = set(filter_rule) - {"field", "op", "values"}
        if extra:
            raise SchemaError(f"unknown filter keys: {sorted(extra)}")
        if filter_rule.get("op") not in FILTER_OPS:
            raise SchemaError(f"unknown filter op: {filter_rule.get('op')}")
        if not isinstance(filter_rule.get("field"), str) or not isinstance(filter_rule.get("values"), list):
            raise SchemaError("filter needs a field and a values list")
    for table in definition["tables"]:
        extra = set(table) - {"table_id", "title", "object_type", "columns", "sort_by"}
        if extra:
            raise SchemaError(f"unknown table keys: {sorted(extra)}")
        if table.get("object_type") not in OBJECT_TYPES:
            raise SchemaError(f"table references unknown object type: {table.get('object_type')}")
        for column in table.get("columns", []):
            if set(column) != {"header", "path"}:
                raise SchemaError("table columns need exactly header and path")
    for layer in definition.get("map_layers", []):
        extra = set(layer) - {"layer_id", "title", "object_types", "geometry_kinds"}
        if extra:
            raise SchemaError(f"unknown map layer keys: {sorted(extra)}")
    timeline = definition.get("timeline", {"include": []})
    for kind in timeline.get("include", []):
        if kind not in TIMELINE_KINDS:
            raise SchemaError(f"unknown timeline kind: {kind}")
    for action in definition.get("actions", []):
        if action not in ANALYST_ACTION_KINDS:
            raise SchemaError(f"unknown workshop action: {action}")
    workflow = definition.get("workflow", {})
    extra = set(workflow) - {"include_requirements", "include_tasks", "include_evidence_requests"}
    if extra:
        raise SchemaError(f"unknown workflow view keys: {sorted(extra)}")
    for table in definition.get("relationship_tables", []):
        extra = set(table) - {"table_id", "title", "relation_types"}
        if extra:
            raise SchemaError(f"unknown relationship table keys: {sorted(extra)}")
        for relation_type in table.get("relation_types", []):
            if relation_type not in RELATION_TYPES:
                raise SchemaError(f"relationship table references unknown relation type: {relation_type}")
    if definition["access"].get("min_role") not in ROLE_RANK:
        raise SchemaError("workshop access needs a known min_role")
    result = dict(definition)
    result["record_type"] = "workshop_definition"
    result.setdefault("relationship_types", [])
    result.setdefault("filters", [])
    result.setdefault("map_layers", [])
    result.setdefault("timeline", {"include": []})
    result.setdefault("alert_rules", [])
    result.setdefault("actions", [])
    result.setdefault("reports", ["json", "markdown", "text"])
    result.setdefault("workflow", {})
    result.setdefault("relationship_tables", [])
    result["definition_sha256"] = sha256({k: v for k, v in result.items() if k != "definition_sha256"})
    return result


def _path_value(record: Mapping[str, Any], path: str) -> Any:
    value: Any = record
    for part in path.split("."):
        if not isinstance(value, Mapping) or part not in value:
            return "UNKNOWN"
        value = value[part]
    return "UNKNOWN" if value is None else value


def _passes_filters(record: Mapping[str, Any], filters: list[Mapping[str, Any]]) -> bool:
    for rule in filters:
        value = _path_value(record, rule["field"])
        if rule["op"] == "eq" and value != rule["values"][0]:
            return False
        if rule["op"] == "in" and value not in rule["values"]:
            return False
    return True


class WorkbenchRenderer:
    def __init__(self, projection: Projection):
        self.projection = projection

    def render(self, definition: Mapping[str, Any], context: AccessContext) -> dict[str, Any]:
        required = definition["access"]["min_role"]
        if context.max_role_rank < ROLE_RANK[required]:
            raise WorkbenchAccessError(f"workshop requires role {required}")
        base = self.projection.view(context)
        objects = [o for o in base["objects"] if o["object_type"] in definition["object_types"]
                   and _passes_filters(o, definition["filters"])]
        object_ids = {o["object_id"] for o in objects}
        relation_types = set(definition["relationship_types"] or RELATION_TYPES)
        relationships = [r for r in base["relationships"] if r["relation_type"] in relation_types
                         and r["source_object_id"] in object_ids and r["target_object_id"] in object_ids]
        tables = []
        for spec in definition["tables"]:
            rows = [{column["header"]: _path_value(record, column["path"]) for column in spec["columns"]}
                    for record in objects if record["object_type"] == spec["object_type"]]
            sort_key = spec.get("sort_by")
            if sort_key:
                header = next((c["header"] for c in spec["columns"] if c["path"] == sort_key), None)
                if header:
                    rows.sort(key=lambda row: str(row[header]))
            tables.append({"table_id": spec["table_id"], "title": spec.get("title", spec["table_id"]),
                           "columns": [c["header"] for c in spec["columns"]], "rows": rows})
        layers = []
        for spec in definition.get("map_layers", []):
            features = []
            for record in objects:
                geometry = record.get("geometry")
                if record["object_type"] not in spec.get("object_types", []) or not geometry:
                    continue
                if spec.get("geometry_kinds") and geometry["kind"] not in spec["geometry_kinds"]:
                    continue
                features.append({
                    "type": "Feature",
                    "geometry": {"type": geometry["kind"].capitalize() if geometry["kind"] != "LINESTRING" else "LineString",
                                 "coordinates": geometry["coordinates"]},
                    "properties": {"object_id": record["object_id"],
                                   "label": (record.get("labels") or [record["object_id"]])[0],
                                   "epistemic_state": record["epistemic_state"],
                                   "symbol": EPISTEMIC_SYMBOLS.get(record["epistemic_state"], "question"),
                                   "freshness": record["freshness"]["state"],
                                   "quality_state": record["quality_summary"]["state"],
                                   "uncertainty_m": geometry.get("uncertainty_m")},
                })
            layers.append({"layer_id": spec["layer_id"], "title": spec.get("title", spec["layer_id"]),
                           "feature_collection": {"type": "FeatureCollection", "features": features}})
        timeline = []
        include = set(definition.get("timeline", {}).get("include", []))
        if "object_version" in include:
            timeline += [{"time": o["recorded_time"], "kind": "object_version",
                          "ref": f"{o['object_id']}@v{o['version']}",
                          "label": f"{o['object_type']} {o['object_id']} v{o['version']} [{o['epistemic_state']}]"}
                         for o in objects]
        if "alert" in include:
            timeline += [{"time": a["recorded_time"], "kind": "alert", "ref": a["alert_id"],
                          "label": f"[{a['severity']}] {a['trigger']} ({a['status']})"} for a in base["alerts"]]
        if "recommendation" in include:
            timeline += [{"time": r["recorded_time"], "kind": "recommendation", "ref": r["recommendation_id"],
                          "label": f"{r['action_kind']}: {r['proposed_action']}"} for r in base["recommendations"]]
        if "decision" in include:
            timeline += [{"time": d["recorded_time"], "kind": "decision", "ref": d["decision_id"],
                          "label": f"{d['state']} by {d['actor_id']}"} for d in base["decisions"]]
        if "analyst_action" in include:
            timeline += [{"time": a["recorded_time"], "kind": "analyst_action", "ref": a["action_id"],
                          "label": f"{a['kind']} {a['subject_id']} by {a['actor_id']}"}
                         for a in base["analyst_actions"]]
        if "association_proposal" in include:
            timeline += [{"time": p["recorded_time"], "kind": "association_proposal", "ref": p["proposal_id"],
                          "label": f"{p['outcome']}: {p['left_object_id']} ~ {p['right_object_id']}"}
                         for p in base["association_proposals"]]
        timeline.sort(key=lambda entry: (entry["time"], entry["ref"]))
        details = {}
        for record in objects:
            involved = [r for r in relationships
                        if record["object_id"] in (r["source_object_id"], r["target_object_id"])]
            details[record["object_id"]] = {
                "current": record, "relationships": involved,
                "provenance": record.get("provenance", {}), "freshness": record["freshness"],
                "quality_summary": record["quality_summary"], "history_count": record["history_count"],
            }
        relationship_tables = []
        for spec in definition.get("relationship_tables", []):
            rows = [{"relation": r["relation_type"], "from": r["source_object_id"], "to": r["target_object_id"],
                     "status": r["status"], "rationale": r.get("rationale", "")}
                    for r in base["relationships"] if r["relation_type"] in spec.get("relation_types", [])]
            relationship_tables.append({"table_id": spec["table_id"], "title": spec.get("title", spec["table_id"]),
                                        "rows": sorted(rows, key=lambda row: (row["relation"], row["from"], row["to"]))})
        workflow_config = definition.get("workflow", {})
        workflow_view = {}
        if workflow_config.get("include_requirements"):
            workflow_view["information_requirements"] = base["information_requirements"]
        if workflow_config.get("include_tasks"):
            workflow_view["analyst_tasks"] = base["analyst_tasks"]
        if workflow_config.get("include_evidence_requests"):
            workflow_view["evidence_requests"] = base["evidence_requests"]
        counts = {"objects": len(objects), "relationships": len(relationships),
                  "alerts": len(base["alerts"]), "open_alerts": base["counts"]["open_alerts"],
                  "recommendations": len(base["recommendations"]), "decisions": len(base["decisions"]),
                  "stale_objects": sum(1 for o in objects if o["freshness"]["state"] == "STALE"),
                  "disputed_objects": sum(1 for o in objects if o["epistemic_state"] == "DISPUTED"),
                  "open_information_requirements": base["counts"]["open_information_requirements"],
                  "open_analyst_tasks": base["counts"]["open_analyst_tasks"]}
        return {"workshop": {"workshop_id": definition["workshop_id"], "version": definition["version"],
                             "purpose": definition["purpose"], "actions": definition.get("actions", [])},
                "meta": base["meta"], "tables": tables, "relationship_tables": relationship_tables,
                "map_layers": layers, "timeline": timeline,
                "alerts": base["alerts"], "recommendations": base["recommendations"],
                "decisions": base["decisions"], "association_proposals": base["association_proposals"],
                "dependence_groups": base["dependence_groups"], "workflow": workflow_view,
                "details": details, "counts": counts}


# ---- common operating picture ----

_SYMBOL_SVG = {
    "circle": '<circle cx="{x}" cy="{y}" r="6" class="sym"/>',
    "square": '<rect x="{x0}" y="{y0}" width="12" height="12" class="sym"/>',
    "triangle": '<polygon points="{x},{yt} {xl},{yb} {xr},{yb}" class="sym"/>',
    "diamond": '<polygon points="{x},{yt} {xr},{y} {x},{yb} {xl},{y}" class="sym"/>',
    "cross": '<path d="M {xl} {yt} L {xr} {yb} M {xr} {yt} L {xl} {yb}" class="sym stroke"/>',
    "question": '<text x="{x}" y="{yq}" class="sym-q">?</text>',
}


def _project_features(layers: list[dict[str, Any]], width: int, height: int, pad: int = 72):
    points = []
    for layer in layers:
        for feature in layer["feature_collection"]["features"]:
            geometry = feature["geometry"]
            coords = geometry["coordinates"]
            if geometry["type"] == "Point":
                points.append(coords[:2])
            else:
                points.extend(c[:2] for c in coords)
    if not points:
        return lambda lon, lat: (width / 2, height / 2)
    lons = [p[0] for p in points]
    lats = [p[1] for p in points]
    min_lon, max_lon = min(lons), max(lons)
    min_lat, max_lat = min(lats), max(lats)
    span_lon = (max_lon - min_lon) or 1e-6
    span_lat = (max_lat - min_lat) or 1e-6

    def project(lon: float, lat: float) -> tuple[float, float]:
        x = pad + (lon - min_lon) / span_lon * (width - 2 * pad)
        y = height - pad - (lat - min_lat) / span_lat * (height - 2 * pad)
        return round(x, 1), round(y, 1)

    return project


def render_cop_html(view: Mapping[str, Any], *, title: str) -> str:
    width, height = 860, 520
    project = _project_features(view["map_layers"], width, height)
    svg_parts = []
    labels = []
    placed_anchors: list[tuple[float, float]] = []
    for layer in view["map_layers"]:
        for feature in layer["feature_collection"]["features"]:
            geometry = feature["geometry"]
            props = feature["properties"]
            state = props["epistemic_state"]
            dashed = state not in ("OBSERVED", "REPORTED", "CORRECTED")
            if geometry["type"] == "LineString":
                points = [project(c[0], c[1]) for c in geometry["coordinates"]]
                path = " ".join(f"{'M' if i == 0 else 'L'} {px} {py}"
                                for i, (px, py) in enumerate(points))
                svg_parts.append(f'<path d="{path}" class="line{" dashed" if dashed else ""}"/>')
                mid = geometry["coordinates"][len(geometry["coordinates"]) // 2]
                x, y = project(mid[0], mid[1])
            else:
                x, y = project(geometry["coordinates"][0], geometry["coordinates"][1])
                template = _SYMBOL_SVG.get(props["symbol"], _SYMBOL_SVG["question"])
                svg_parts.append(template.format(x=x, y=y, x0=x - 6, y0=y - 6, xl=x - 6, xr=x + 6,
                                                 yt=y - 6, yb=y + 6, yq=y + 4))
            # Nudge a label down in fixed steps until it clears the ones
            # already placed, and anchor it from the right near the edge so it
            # stays inside the SVG. Deterministic, with no per-scenario rules.
            right_aligned = x > width - 190
            label_x, label_y = (x - 9 if right_aligned else x + 9), y - 4
            while any(abs(label_x - px) < 110 and abs(label_y - py) < 14 for px, py in placed_anchors):
                label_y += 13
            label_y = min(label_y, height - 10)
            placed_anchors.append((label_x, label_y))
            anchor = ' text-anchor="end"' if right_aligned else ""
            labels.append(f'<text x="{label_x}" y="{label_y}" class="lbl"{anchor}>{html.escape(str(props["label"]))}'
                          f' <tspan class="tag">[{state}{"/STALE" if props["freshness"] == "STALE" else ""}]</tspan></text>')
    alert_rows = "".join(
        f'<li><b>[{html.escape(a["severity"])}]</b> {html.escape(a["trigger"])} '
        f'<i>({html.escape(a["status"])})</i></li>' for a in view["alerts"])
    table_html = []
    for table in view["tables"]:
        head = "".join(f'<th scope="col">{html.escape(str(c))}</th>' for c in table["columns"])
        body = "".join("<tr>" + "".join(f"<td>{html.escape(str(row[c]))}</td>" for c in table["columns"]) + "</tr>"
                       for row in table["rows"])
        table_html.append(f'<h3>{html.escape(table["title"])}</h3>'
                          f'<table><caption>{html.escape(table["title"])} — visible records only</caption>'
                          f'<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>')
    timeline_rows = "".join(f'<li><code>{html.escape(entry["time"])}</code> {html.escape(entry["label"])}</li>'
                            for entry in view["timeline"])
    counts = view["counts"]
    meta = view["meta"]
    map_description = (f'{counts["objects"]} visible objects and {counts["relationships"]} visible relationships. '
                       f'{counts["stale_objects"]} stale and {counts["disputed_objects"]} disputed objects. '
                       'Labels state epistemic status in text; dashed lines are non-observed states.')
    return f"""<!doctype html>
<!-- generated by curunir_operational.workbench; self-contained; no external assets -->
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
body{{font-family:system-ui,sans-serif;margin:1.2rem;max-width:960px}}
.banner{{border:2px solid #444;padding:.4rem .8rem;font-weight:600;margin-bottom:1rem}}
svg{{border:1px solid #999;background:#f6f8f7;width:100%;max-width:{width}px;height:auto}}
table{{display:block;overflow-x:auto;max-width:100%}}
.line{{fill:none;stroke:#333;stroke-width:2.5}}
.line.dashed{{stroke-dasharray:7 5}}
.sym{{fill:#fff;stroke:#222;stroke-width:2}}
.sym.stroke{{fill:none}}
.sym-q{{font-size:14px;font-weight:700}}
.lbl{{font-size:11px}}
.tag{{font-size:9px;fill:#555}}
table{{border-collapse:collapse;margin:.4rem 0 1rem}}
caption{{font-size:.8rem;text-align:left;color:#444;padding:.15rem 0}}
td,th{{border:1px solid #aaa;padding:.15rem .5rem;font-size:.85rem;text-align:left}}
.counts span{{margin-right:1.2rem}}
.legend li{{font-size:.85rem}}
@media (max-width:800px){{body{{margin:.7rem}}svg{{min-height:390px}}.lbl{{font-size:13px}}.tag{{font-size:11px}}}}
</style>
</head><body><main>
<div class="banner">RESEARCH SHADOW — SYNTHETIC DATA — NOT AN OPERATIONAL SYSTEM OF RECORD<br>
Snapshot {html.escape(meta["snapshot_time"])} · context {html.escape(meta["context_id"])} · state {html.escape(meta["state_token"])}</div>
<h1>{html.escape(title)}</h1>
<p class="counts"><span>Objects: {counts["objects"]}</span><span>Relationships: {counts["relationships"]}</span>
<span>Open alerts: {counts["open_alerts"]}</span><span>Stale: {counts["stale_objects"]}</span>
<span>Disputed: {counts["disputed_objects"]}</span><span>Decisions: {counts["decisions"]}</span></p>
<h2>Operating picture</h2>
<svg viewBox="0 0 {width} {height}" role="img" aria-labelledby="map-title map-description">
<title id="map-title">Operating picture map — symbol shapes carry epistemic state</title>
<desc id="map-description">{html.escape(map_description)}</desc>
{"".join(svg_parts)}
{"".join(labels)}
</svg>
<ul class="legend">
<li>Symbol shapes carry epistemic state (never colour alone): circle=observed, square=reported/extracted,
triangle=inferred, diamond=planned/predicted, cross=disputed/cancelled, ?=unknown. Dashed lines are
non-observed states. Text tags repeat the state and staleness.</li>
</ul>
<h2>Alerts</h2><ul>{alert_rows or "<li>none visible in this context</li>"}</ul>
{"".join(table_html)}
<h2>Timeline</h2><ul>{timeline_rows or "<li>empty</li>"}</ul>
</main></body></html>
"""
