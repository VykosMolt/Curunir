// Workbench views, part 3: the report / decision-dossier engine.
import { get, post, token } from "./api.js";
import { badge, clip, emptyBox, errorBox, fmtTime, h, kv, pivot, refLink, table } from "./ui.js";
import { annotationsSection, nav, render } from "./views.js";

const SECTION_KINDS = ["executive_summary", "mission_question", "key_judgments",
  "current_situation", "evidence", "themes", "key_events", "stakeholders",
  "narratives", "impact_exposure", "hypotheses", "forecasts", "warnings",
  "information_gaps", "decision_options", "unresolved_risks", "dissent",
  "collection_status", "appendix"];

export async function reportsView(main) {
  const refresh = () => reportsView(main);
  await render(main, async () => {
    const res = await get("/api/family/workbench_report");
    const title = h("input", { placeholder: "report title…", size: 40 });
    const question = h("input", { placeholder: "mission question…", size: 50 });
    const status = h("div", {});
    return [h("h1", {}, "Reports / dossiers"),
      status,
      table({ columns: [
        { label: "title", render: (r) => pivot(`/reports/${r.report_id}`, r.title) },
        { label: "status", render: (r) => badge(r.status) },
        { label: "v", key: "version" },
        { label: "author", key: "author" },
        { label: "updated", render: (r) => fmtTime(r.recorded_time) }],
        rows: res.records }),
      h("div", { class: "card" },
        h("h3", {}, "New dossier"),
        h("div", { class: "toolbar" }, title, question,
          h("button", { class: "primary", onclick: async () => {
            try {
              const made = await post("/api/commands/reports", {
                title: title.value, question: question.value,
                sections: [{ kind: "key_judgments", title: "Key judgments", sentences: [] }] });
              nav(`/reports/${made.report_id}`);
            } catch (err) { status.replaceChildren(errorBox(err)); }
          } }, "Create draft")))];
  });
}

function sentenceEditor(sentence = {}) {
  const originalText = sentence.text || "";
  const textEl = h("textarea", { placeholder: "one atomic statement…" }, originalText);
  const statusEl = h("select", {},
    ["SUPPORTED", "EXPLICITLY_INFERENTIAL", "UNRESOLVED"].map((s) =>
      h("option", { selected: s === sentence.status || undefined }, s)));
  const basisEl = h("input", { placeholder: "basis refs (comma-separated ids)", size: 52,
    value: (sentence.basis_refs || []).join(",") });
  const assumptionsEl = h("input", { placeholder: "assumption ids (comma-separated)", size: 52,
    value: (sentence.assumption_ids || []).join(",") });
  const inferenceEl = h("input", { placeholder: "inference note (required if inferential)", size: 52,
    value: sentence.inference_note || "" });
  const unresolvedEl = h("input", { placeholder: "unresolved reason (required if unresolved)", size: 52,
    value: sentence.unresolved_reason || "" });
  const scopeEl = h("select", {}, ["", "CURRENT", "HISTORICAL"].map((s) =>
    h("option", { selected: s === (sentence.temporal_scope || "") || undefined }, s || "(no temporal claim)")));
  const independentEl = h("input", { type: "checkbox", checked: sentence.asserts_independent || undefined });
  const wrap = h("div", { class: "sentence" },
    textEl,
    h("div", { class: "toolbar" },
      h("label", {}, "status"), statusEl,
      h("label", {}, "temporal"), scopeEl,
      h("label", {}, h("span", {}, independentEl, " asserts independent sources")),
      h("button", { class: "danger", onclick: () => wrap.remove() }, "remove")),
    basisEl, assumptionsEl, inferenceEl, unresolvedEl);
  wrap.value = () => ({
    // keep a stable id while the text is unchanged, so annotations and
    // dissent stay anchored; a rewritten sentence is a new statement
    sentence_id: textEl.value === originalText ? sentence.sentence_id : undefined,
    text: textEl.value, status: statusEl.value,
    basis_refs: basisEl.value.split(",").map((s) => s.trim()).filter(Boolean),
    assumption_ids: assumptionsEl.value.split(",").map((s) => s.trim()).filter(Boolean),
    inference_note: inferenceEl.value, unresolved_reason: unresolvedEl.value,
    temporal_scope: scopeEl.value === "(no temporal claim)" ? "" : scopeEl.value,
    asserts_independent: independentEl.checked,
  });
  return wrap;
}

function sectionEditor(section = {}) {
  const kindEl = h("select", {}, SECTION_KINDS.map((k) =>
    h("option", { selected: k === section.kind || undefined }, k)));
  const titleEl = h("input", { value: section.title || "", placeholder: "section title", size: 36 });
  const sentenceHost = h("div", {}, (section.sentences || []).map(sentenceEditor));
  const wrap = h("div", { class: "card" },
    h("div", { class: "toolbar" }, kindEl, titleEl,
      h("button", { onclick: () => sentenceHost.append(sentenceEditor()) }, "+ sentence"),
      h("button", { class: "danger", onclick: () => wrap.remove() }, "remove section")),
    sentenceHost);
  wrap.value = () => ({
    // the section id stays stable across kind/title edits, so sentence ids
    // and their annotations survive a retitle
    section_id: section.section_id,
    kind: kindEl.value, title: titleEl.value,
    sentences: [...sentenceHost.children].map((c) => c.value()),
    option_ids: section.option_ids || [],
  });
  return wrap;
}

function sentenceDisplay(sentence) {
  const note = sentence.status === "EXPLICITLY_INFERENTIAL" ? sentence.inference_note
    : sentence.status === "UNRESOLVED" ? sentence.unresolved_reason : "";
  const assumptions = (sentence.assumption_ids || []).length
    ? ` · assumptions: ${(sentence.assumption_ids || []).length}` : "";
  return h("div", { class: `sentence s-${sentence.status.toLowerCase()}` },
    h("div", {}, sentence.text, " ", badge(sentence.status),
      sentence.temporal_scope ? badge(sentence.temporal_scope) : null,
      sentence.asserts_independent ? badge("ASSERTS_INDEPENDENT") : null),
    h("div", { class: "meta" },
      (sentence.basis_refs || []).length
        ? ["basis: ", sentence.basis_refs.map((r) =>
            h("span", {}, refLink("semantic_claim", r, clip(r, 26)), " "))]
        : "no basis refs",
      note ? ` · ${note}` : "",
      (sentence.assumption_ids || []).length
        ? ` · assumptions: ${sentence.assumption_ids.length}` : ""));
}

export async function reportView(main, params, id) {
  const refresh = () => reportView(main, params, id);
  const editing = params.get("edit") === "1";
  await render(main, async () => {
    const [rec, validation, dispositions] = await Promise.all([
      get(`/api/record/workbench_report/${encodeURIComponent(id)}`),
      get(`/api/reports/${encodeURIComponent(id)}/validate`),
      get(`/api/reports/${encodeURIComponent(id)}/dispositions`),
    ]);
    const r = rec.current;
    const status = h("div", {});
    const act = (fn) => async () => {
      try { await fn(); refresh(); }
      catch (err) {
        if (err.status === 422 && err.payload?.findings) {
          status.replaceChildren(h("div", { class: "notice bad" },
            h("b", {}, "validation rejected approval:"),
            h("ul", {}, err.payload.findings.map((f) =>
              h("li", {}, h("b", {}, f.code), ` — ${f.detail} `,
                h("span", { class: "faint" }, `(“${clip(f.text, 60)}”)`))))));
        } else status.replaceChildren(errorBox(err));
      }
    };

    const header = [
      h("h1", {}, r.title, " ", badge(r.status), h("span", { class: "faint" }, ` v${r.version}`)),
      h("p", { class: "muted" }, r.question),
      h("p", { class: "faint mono" },
        `author ${r.author} · basis state ${r.based_on_state_token}` +
        (validation.stale_basis_state ? " (STALE — mission state moved since drafting)" : "")),
      status,
    ];

    const validationPanel = h("div", {
      class: `notice ${validation.ok ? "" : "bad"}` },
      validation.ok
        ? h("span", {}, "validation: no blocking findings",
            validation.findings.length ? ` (${validation.findings.length} advisory)` : "")
        : [h("b", {}, `validation: ${validation.blocking.length} blocking finding(s)`),
           h("ul", {}, validation.blocking.map((f) =>
             h("li", {}, h("b", {}, f.code), ` — ${f.detail} `,
               h("span", { class: "faint" }, `(“${clip(f.text, 60)}”)`))))]);

    if (editing) {
      const sectionHost = h("div", {}, r.sections.map(sectionEditor));
      return [...header,
        h("h2", {}, "Edit (a save is a new version; lineage per sentence)"),
        sectionHost,
        h("div", { class: "toolbar" },
          h("button", { onclick: () => sectionHost.append(sectionEditor()) }, "+ section"),
          h("span", { class: "spacer" }),
          h("button", { onclick: () => nav(`/reports/${id}`) }, "Cancel"),
          h("button", { class: "primary", onclick: act(async () => {
            await post(`/api/commands/reports/${id}/edit`, {
              expected_version: r.version,
              sections: [...sectionHost.children].map((c) => c.value()) });
            nav(`/reports/${id}`);
          }) }, `Save as v${r.version + 1}`))];
    }

    const roleTabs = h("div", { class: "toolbar" },
      h("span", { class: "muted" }, "role projections:"),
      ["ANALYST", "EXECUTIVE", "SOURCE_LEGAL", "OPERATOR"].map((role) =>
        h("button", { onclick: async () => {
          try {
            const view = await get(`/api/reports/${id}/role/${role}`);
            roleHost.replaceChildren(
              h("h3", {}, `${role} projection (same record, v${view.version})`),
              role === "EXECUTIVE" && view.uncertainty_note
                ? h("p", { class: "muted" },
                    `epistemic mix: ${view.uncertainty_note.SUPPORTED} supported · ` +
                    `${view.uncertainty_note.EXPLICITLY_INFERENTIAL} inferential · ` +
                    `${view.uncertainty_note.UNRESOLVED} unresolved`) : null,
              view.sections.length
                ? view.sections.map((s) => [h("h3", {}, s.title),
                    s.sentences.map(sentenceDisplay)])
                : emptyBox("no sections in this projection"),
              role === "OPERATOR" && view.decision_options?.length
                ? [h("h3", {}, "Decision options"), view.decision_options.map((o) =>
                    h("div", { class: "card" }, clip(o.description, 160), " ", badge(o.status)))] : null);
          } catch (err) { status.replaceChildren(errorBox(err)); }
        } }, role)));
    const roleHost = h("div", {});

    return [...header, validationPanel,
      h("div", { class: "toolbar" },
        ["DRAFT", "RETURNED_FOR_REVISION"].includes(r.status)
          ? [h("button", { onclick: () => nav(`/reports/${id}?edit=1`) }, "Edit"),
             h("button", { class: "primary", onclick: act(async () => {
               await post(`/api/commands/reports/${id}/submit`, { expected_version: r.version });
             }) }, "Submit for review")] : null,
        ["APPROVED", "APPROVED_WITH_DISSENT"].includes(r.status)
          ? h("button", { onclick: () => nav(`/reports/${id}?edit=1`) }, "Revise (new draft version)") : null,
        r.status === "IN_REVIEW"
          ? [h("button", { class: "primary", onclick: act(async () => {
               // the server's dissent set covers sentence/section anchors
               // across every version — the UI drives the dialog from it
               const dissent = validation.open_dissent || [];
               await post(`/api/commands/reports/${id}/approve`, {
                 expected_version: r.version,
                 acknowledge_dissent: dissent.length && confirm(
                   `${dissent.length} open dissent annotation(s) exist ` +
                   `(${dissent.map((a) => a.author).join(", ")}). ` +
                   "Approve WITH dissent (kept visible on the disposition)?")
                   ? dissent.map((a) => a.annotation_id) : [] });
             }) }, "Approve (validated, human act)"),
             h("button", { onclick: act(async () => {
               const note = prompt("return-for-revision note:") || "";
               if (!note) throw new Error("a note is required");
               await post(`/api/commands/reports/${id}/reject`, {
                 expected_version: r.version, note, return_for_revision: true });
             }) }, "Return for revision"),
             h("button", { class: "danger", onclick: act(async () => {
               const note = prompt("rejection note:") || "";
               if (!note) throw new Error("a note is required");
               await post(`/api/commands/reports/${id}/reject`, {
                 expected_version: r.version, note });
             }) }, "Reject")] : null,
        h("span", { class: "spacer" }),
        h("a", { href: `/api/reports/${id}/export`, target: "_blank",
          onclick: (e) => { e.preventDefault(); exportWithAuth(`/api/reports/${id}/export`, `${id}.json`); } }, "export JSON"),
        h("a", { href: `/api/reports/${id}/export.html`, target: "_blank",
          onclick: (e) => { e.preventDefault(); exportWithAuth(`/api/reports/${id}/export.html`, `${id}.html`, true); } }, "export HTML")),
      r.sections.map((s) => [h("h2", {}, s.title, " ", h("span", { class: "faint" }, s.kind)),
        s.sentences.length ? s.sentences.map(sentenceDisplay) : emptyBox("empty section"),
        (s.option_ids || []).map((oid) => h("div", {}, refLink("response_option", oid, "decision option")))]),
      roleTabs, roleHost,
      h("h2", {}, "Version history"),
      table({ columns: [
        { label: "v", key: "version" },
        { label: "status", render: (v) => badge(v.status) },
        { label: "author", key: "author" },
        { label: "note", render: (v) => clip(v.change_note, 70) },
        { label: "recorded", render: (v) => fmtTime(v.recorded_time) }],
        rows: rec.versions.slice().reverse() }),
      h("h2", {}, "Dispositions (attributable, immutable)"),
      table({ columns: [
        { label: "disposition", render: (d) => badge(d.disposition) },
        { label: "on version", key: "report_version" },
        { label: "actor", render: (d) => `${d.actor_id} (${d.actor_kind})` },
        { label: "note", render: (d) => clip(d.note, 60) },
        { label: "validation", render: (d) => d.validation_sha256
            ? h("span", { class: "mono faint" }, clip(d.validation_sha256, 14)) : "" },
        { label: "time", render: (d) => fmtTime(d.recorded_time) }],
        rows: dispositions.dispositions.slice().reverse() }),
      annotationsSection(rec.annotations, "workbench_report", id, refresh),
    ];
  });
}

async function exportWithAuth(path, filename, isHtml = false) {
  const response = await fetch(path, { headers: { Authorization: `Bearer ${token()}` } });
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = filename; a.click();
  URL.revokeObjectURL(url);
}
