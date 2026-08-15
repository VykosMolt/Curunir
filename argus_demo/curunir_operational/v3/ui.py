"""Self-contained V3 collaboration workbench rendering."""
from __future__ import annotations

import html
from typing import Any, Mapping

from ..canonical import sha256


def _map_svg(payloads: list[Mapping[str, Any]]) -> str:
    features = [(payload.get("subject_id", "UNKNOWN"), payload.get("geometry"),
                 payload.get("status") or payload.get("value") or payload.get("state") or "UNKNOWN")
                for payload in payloads if payload.get("geometry")]
    points = []
    for _, geometry, _ in features:
        coords = geometry.get("coordinates", ())
        points.extend(coords if geometry.get("type") == "LineString" else [coords])
    if not points:
        return '<svg viewBox="0 0 900 360" role="img" aria-labelledby="map-title map-desc"><title id="map-title">Authorized operating picture</title><desc id="map-desc">No geospatial records are visible in this access context.</desc><text x="40" y="80">No geospatial records visible</text></svg>'
    lons, lats = [p[0] for p in points], [p[1] for p in points]
    lo0, lo1, la0, la1 = min(lons), max(lons), min(lats), max(lats)
    def project(point):
        x = 75 + (point[0] - lo0) / ((lo1 - lo0) or 1) * 750
        y = 300 - (point[1] - la0) / ((la1 - la0) or 1) * 240
        return round(x, 1), round(y, 1)
    shapes, labels = [], []
    for subject, geometry, state in features:
        coords = geometry["coordinates"]
        if geometry["type"] == "LineString":
            path = " ".join(f"{'M' if index == 0 else 'L'} {project(point)[0]} {project(point)[1]}"
                            for index, point in enumerate(coords))
            shapes.append(f'<path d="{path}" class="route"/>')
            x, y = project(coords[len(coords)//2])
        else:
            x, y = project(coords); shapes.append(f'<rect x="{x-6}" y="{y-6}" width="12" height="12" class="object"/>')
        anchor = "end" if x > 700 else "start"; lx = x - 10 if anchor == "end" else x + 10
        labels.append(f'<text x="{lx}" y="{max(18,y-8)}" text-anchor="{anchor}">{html.escape(str(subject))} [{html.escape(str(state))}]</text>')
    description = f"{len(features)} authorized geospatial records. Text labels state status; dashed routes indicate non-observed or disputed planning state."
    return f'<svg viewBox="0 0 900 360" role="img" aria-labelledby="map-title map-desc"><title id="map-title">Authorized operating picture</title><desc id="map-desc">{html.escape(description)}</desc>{"".join(shapes)}{"".join(labels)}</svg>'


def render_collaboration_workbench(projection: Mapping[str, Any], *, title: str,
                                   workbench_id: str, record_types: tuple[str, ...]) -> str:
    states = [state for state in projection["state"] if state["record_type"] in record_types]
    union = [event for event in projection["union_records"]
             if event["payload"].get("record_type") in record_types]
    payloads = [value for state in states for value in state["values"]] + [event["payload"] for event in union]
    state_rows = "".join(
        f'<tr><td>{html.escape(item["record_type"])}</td><td>{html.escape(item["subject_id"])}</td>'
        f'<td>{html.escape(item["status"])}</td><td>{html.escape(str(item["values"]))}</td>'
        f'<td>{html.escape(item["merge_policy"])}</td></tr>' for item in states) or '<tr><td colspan="5">None visible</td></tr>'
    conflict_rows = "".join(
        f'<tr><td>{html.escape(item["conflict_type"])}</td><td>{html.escape(item["affected_object_or_workflow"])}</td>'
        f'<td>{html.escape(item["causal_relationship"])}</td><td>{html.escape(item["status"])}</td>'
        f'<td>{html.escape(str(item["competing_values"]))}</td></tr>' for item in projection["conflicts"]) \
        or '<tr><td colspan="5">No visible conflict record</td></tr>'
    activity_rows = "".join(
        f'<tr><td>{html.escape(event["payload"].get("record_type", ""))}</td>'
        f'<td>{html.escape(event["payload"].get("subject_id", ""))}</td>'
        f'<td>{html.escape(event["actor_id"])}</td><td>{html.escape(event["recorded_time"])}</td>'
        f'<td>{html.escape(str(event["payload"].get("evidence_snapshot", ())))}</td></tr>' for event in union) \
        or '<tr><td colspan="5">No visible collaboration activity</td></tr>'
    map_svg = _map_svg(payloads)
    integrity = sha256({"workbench": workbench_id, "projection": projection["opaque_state_token"],
                        "types": record_types})
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title><style>
body{{font-family:system-ui,sans-serif;max-width:1180px;margin:1rem auto;padding:0 .8rem;background:#f4f6f7;color:#16202a}}
.banner{{border:2px solid #34516d;background:#e8eef3;padding:.6rem;font-weight:700}}.notice{{border-left:5px solid #8b5e00;padding:.5rem;background:#fff6df}}
.cards{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:.7rem}}.card{{background:white;border:1px solid #8997a3;padding:.7rem}}
svg{{width:100%;height:auto;background:white;border:1px solid #8997a3}}.route{{fill:none;stroke:#263b4e;stroke-width:4;stroke-dasharray:10 5}}.object{{fill:#fff;stroke:#263b4e;stroke-width:3}}svg text{{font-size:13px}}
section{{margin:1rem 0}}table{{border-collapse:collapse;width:100%;background:white}}caption{{text-align:left;font-weight:650;padding:.25rem}}
th,td{{border:1px solid #94a0aa;padding:.35rem;text-align:left;vertical-align:top;font-size:.83rem}}th{{background:#e5ebf0}}:focus{{outline:3px solid #c65;outline-offset:2px}}
@media(max-width:800px){{.cards{{grid-template-columns:1fr}}section{{overflow-x:auto}}svg text{{font-size:16px}}}}
</style></head><body><main>
<div class="banner">RESEARCH SHADOW · SYNTHETIC DISTRIBUTED SCENARIO · NOT A SYSTEM OF RECORD</div>
<h1>{html.escape(title)}</h1><p>{html.escape(workbench_id)}</p>
<p class="notice">This access-aware view counts visible records only. It intentionally discloses no omitted counts, identifiers, sequence gaps, compartments, hidden conflicts or restricted provenance.</p>
<div class="cards"><div class="card"><b>Visible materialized states</b><br>{len(states)}</div><div class="card"><b>Visible conflicts</b><br>{len(projection["conflicts"])}</div><div class="card"><b>Knowledge token</b><br>{html.escape(projection["opaque_state_token"])}</div></div>
<section><h2>Authorized operating picture</h2>{map_svg}<p>Map labels repeat state in text. Route dashes and conflict rows ensure color is never the only signal.</p></section>
<section><h2>Conflict-aware state</h2><table><caption>Current authorized state; conflicts are not collapsed by last-write-wins</caption><thead><tr><th scope="col">Type</th><th scope="col">Subject</th><th scope="col">State</th><th scope="col">Competing/current values</th><th scope="col">Merge policy</th></tr></thead><tbody>{state_rows}</tbody></table></section>
<section><h2>Distributed conflicts</h2><table><caption>Visible conflicts and resolution state</caption><thead><tr><th scope="col">Type</th><th scope="col">Subject</th><th scope="col">Causal relation</th><th scope="col">Status</th><th scope="col">Values</th></tr></thead><tbody>{conflict_rows}</tbody></table></section>
<section><h2>Tasks, handoffs, evidence and decisions</h2><table><caption>Append-only collaboration timeline with evidence snapshots</caption><thead><tr><th scope="col">Record</th><th scope="col">Subject</th><th scope="col">Actor</th><th scope="col">Recorded</th><th scope="col">Evidence</th></tr></thead><tbody>{activity_rows}</tbody></table></section>
<p><small>Workbench integrity {integrity}</small></p></main></body></html>"""
