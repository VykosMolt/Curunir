// The evidence slide-over: from a claim to the exact captured bytes.
//
// This is the panel that has to earn the product's central promise. It shows
// the descent as steps a person can follow -- claim, observation, anchor,
// capture, source -- and then shows the ACTUAL BYTES with the anchored span
// highlighted where it sits. Not a hash. The text.

import { get } from "./api.js";
import { el, handle, humanPredicate, ident, skeleton, when } from "./render.js";

let host = null;
export function mountAside(node) { host = node; }

export function closeAside() {
  if (!host) return;
  host.classList.add("hidden");
  document.querySelector(".shell")?.classList.remove("with-aside");
  document.querySelectorAll('[aria-selected="true"]').forEach(n => n.setAttribute("aria-selected", "false"));
}

function open(title, body) {
  host.replaceChildren(
    el("header", {},
      el("h2", {}, title),
      el("button", { class: "close", title: "Close (Esc)", onclick: closeAside, "aria-label": "Close" }, "×")),
    el("div", { class: "body" }, body));
  host.classList.remove("hidden");
  document.querySelector(".shell")?.classList.add("with-aside");
}

/** Choose the representation the anchor's offsets actually refer to.
 *  An anchor carries `normalized_sha256`; its start/end index the NORMALIZED
 *  text, not the raw capture. Highlighting those offsets in the raw bytes puts
 *  the mark in the wrong place, which is worse than not marking at all. */
function representationFor(ev, anchor) {
  const norms = ev?.normalized_payloads || [];
  const match = norms.find(n => n && anchor?.normalized_sha256 && n.sha256 === anchor.normalized_sha256);
  if (match?.text) return { text: match.text, kind: "normalized text" };
  if (norms[0]?.text) return { text: norms[0].text, kind: "normalized text" };
  return { text: ev?.payload?.text, kind: "captured bytes" };
}

/** Render text with the anchored span highlighted, trimmed to a window.
 *  The offsets are TRUSTED ONLY IF the slice they name equals the anchor's
 *  recorded exact_value. Otherwise fall back to locating that value, and if
 *  even that fails, mark nothing and say so. */
function bytesWithSpan(text, anchor) {
  const box = el("pre", { class: "bytes" });
  if (!text) { box.append("(no text representation preserved)"); return box; }
  let start = Number(anchor?.start), end = Number(anchor?.end);
  const exact = anchor?.exact_value;
  const agrees = Number.isFinite(start) && Number.isFinite(end) && end > start
    && end <= text.length && (!exact || text.slice(start, end) === exact);
  if (!agrees && exact) {
    const found = text.indexOf(exact);
    if (found >= 0) { start = found; end = found + exact.length; }
    else {
      box.append(text.slice(0, 4000));
      box.append(el("span", {}, ""));
      return box;
    }
  }
  const usable = Number.isFinite(start) && Number.isFinite(end) && end > start && end <= text.length;
  if (!usable) { box.append(text.slice(0, 4000)); return box; }
  const pad = 900;
  const from = Math.max(0, start - pad), to = Math.min(text.length, end + pad);
  if (from > 0) box.append(el("span", {}, "…"));
  box.append(text.slice(from, start));
  box.append(el("mark", { title: "the exact anchored span" }, text.slice(start, end)));
  box.append(text.slice(end, to));
  if (to < text.length) box.append(el("span", {}, "…"));
  return box;
}

function step(label, what, whisper, extra) {
  return el("li", {},
    el("div", { class: "step" }, label),
    el("div", { class: "what" }, what),
    whisper ? el("div", { class: "whisper" }, whisper) : null,
    extra || null);
}

export async function showClaimEvidence(claimId, trigger) {
  document.querySelectorAll('[aria-selected="true"]').forEach(n => n.setAttribute("aria-selected", "false"));
  trigger?.setAttribute("aria-selected", "true");
  open("Evidence", skeleton("following the descent…"));
  let descent;
  try { descent = await get(`/api/claims/${encodeURIComponent(claimId)}/descent`); }
  catch (err) { open("Evidence", el("p", { class: "error" }, `could not descend: ${err.message}`)); return; }

  const claim = descent.claim?.record || {};
  const steps = el("ul", { class: "descent" });

  steps.append(step("Claim",
    `${humanPredicate(claim.predicate)} — ${claim.object_or_value ?? ""}`,
    [`${descent.independent_basis_count ?? 0} independent basis`,
     claim.polarity && claim.polarity !== "AFFIRMED" ? ` · ${claim.polarity.toLowerCase()}` : ""].join(""),
    ident(claim.claim_id)));

  const observations = descent.observations || [];
  if (!observations.length) steps.append(step("Observation", "no observation recorded", "this claim has no descent"));

  for (const obs of observations) {
    const rec = obs.record || {};
    const anchor = (rec.anchors || [])[0] || {};
    steps.append(step("Observation",
      rec.value ?? rec.observed_value ?? obs.label ?? "",
      [rec.attribute ? humanPredicate(rec.attribute) : "", rec.language ? ` · ${rec.language}` : ""].join(""),
      ident(rec.observation_id || obs.id)));

    if (anchor.manifestation_id) {
      let ev = null;
      try { ev = await get(`/api/evidence/${encodeURIComponent(anchor.manifestation_id)}`); } catch { /* shown below */ }
      const man = ev?.manifestation || {};
      const src = ev?.source || {};
      const rep = representationFor(ev, anchor);
      const located = rep.text && anchor?.exact_value
        ? rep.text.includes(anchor.exact_value) : false;
      const captured = man.archive_capture_time || man.retrieval_time;
      steps.append(step("Captured evidence",
        man.final_url || man.native_id || anchor.manifestation_id,
        [src.publisher_name || man.source_id || "",
         captured ? ` · ${when(captured, { full: true })}` : "",
         src.official_status === "OFFICIAL" ? " · official source" : ""].join(""),
        el("div", {},
          ident(`${anchor.manifestation_id} · sha256 ${man.content_sha256 || ""}`),
          bytesWithSpan(rep.text, anchor),
          el("div", { class: "whisper" },
            located
              ? `highlighted in the ${rep.kind} of this capture`
              : `the recorded span could not be located in the ${rep.kind}; nothing is highlighted`))));
    }
  }
  open("Evidence", steps);
}

export async function showEntity(objectId) {
  open("Entity", skeleton("loading…"));
  try {
    const d = await get(`/api/entities/${encodeURIComponent(objectId)}`);
    const o = d.object || d;
    open("Entity", el("div", {},
      el("div", { class: "eyebrow" }, o.object_type || "object"),
      el("h1", { class: "title" }, (o.labels || [])[0] || `object ${handle(objectId)}`),
      el("p", { class: "subtitle" },
        `${o.lifecycle || ""}${o.epistemic_state ? " · " + String(o.epistemic_state).toLowerCase() : ""}`),
      ident(objectId)));
  } catch (err) { open("Entity", el("p", { class: "error" }, err.message)); }
}
