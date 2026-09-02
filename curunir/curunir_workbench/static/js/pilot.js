// The V6.8 pilot panel: session control and the frozen mission brief.
// Only reachable when the server carries the /v68/pilot/* routes; the plain
// workbench has none and shows nothing.
import { clearToken, get, post } from "./api.js";
import { busy,
  badge, clip, emptyBox, errorBox, field, fmtTime, h, refLink, table,
} from "./ui.js";
import { render } from "./views.js";

const ROLES = ["PRIMARY_OPERATOR", "APPROVER", "PUBLIC_ACCESS_CHECK"];

export async function pilotView(main) {
  const refresh = () => pilotView(main);
  await render(main, async () => {
    const [status, brief] = await Promise.all([
      get("/v68/pilot/status"),
      get("/v68/pilot/brief").catch((err) => ({ error: err })),
    ]);
    const open = status.open_sessions || [];
    const mine = open.filter((s) => s.actor_id === status.actor_id);

    const role = h("select", { "aria-label": "participant role" },
      ROLES.map((r) => h("option", { value: r,
        selected: r === (mine[0] || {}).participant_role || undefined }, r)));
    const note = h("input", { placeholder: "note…", size: 60,
      "aria-label": "session note" });
    const sessionStatus = h("div", {});

    const control = (path) => async () => {
      sessionStatus.replaceChildren();
      try {
        await post(path, { participant_role: role.value, note: note.value });
      } catch (err) {
        sessionStatus.replaceChildren(errorBox(err));
        return;
      }
      if (path.endsWith("/end")) {
        // Nothing can be read after the end: the token goes and the page
        // returns to the sign-in form, so a stray click or reload falls
        // outside no session.
        clearToken();
        location.reload();
        return;
      }
      refresh();
    };

    const correctionNote = h("input", { placeholder: "what was revised, and why…",
      size: 60, "aria-label": "operator correction note" });
    const correctionStatus = h("div", {});
    const recordCorrection = async () => {
      correctionStatus.replaceChildren();
      if (!correctionNote.value.trim()) {
        correctionStatus.replaceChildren(
          errorBox(new Error("an operator correction requires a note")));
        return;
      }
      try {
        const done = await post("/v68/pilot/correction",
          { participant_role: role.value, note: correctionNote.value });
        correctionNote.value = "";
        correctionStatus.replaceChildren(h("div", { class: "notice" },
          `operator correction recorded as pilot event ${done.event_seq}`));
      } catch (err) {
        correctionStatus.replaceChildren(errorBox(err));
      }
    };

    return [
      h("h1", {}, "Pilot session"),
      h("p", { class: "muted" },
        "Start the session before any other work: the harness bounds every " +
        "recorded action to an open start/end pair, and a read made before " +
        "the start falls outside it."),
      h("div", { class: "card" },
        h("h2", {}, "Session"),
        field("participant role", role),
        field("note", note),
        h("div", { class: "toolbar" },
          h("button", { class: "primary", onclick: busy(control("/v68/pilot/start")) },
            "Start session"),
          h("button", { onclick: busy(control("/v68/pilot/end")) }, "End session")),
        sessionStatus,
        open.length
          ? h("div", {}, h("p", { class: "muted" }, "open sessions"),
              open.map((s) => h("div", {}, badge(s.participant_role), " ", s.actor_id,
                s.actor_id === status.actor_id
                  ? h("span", { class: "faint" }, " · yours") : null)))
          : emptyBox("no open session"),
        h("p", { class: "faint" },
          `pilot log: ${status.event_count} events · ` +
          `chain ${status.valid ? "verified" : "INVALID"}`)),
      h("div", { class: "card" },
        h("h2", {}, "Operator correction"),
        h("p", { class: "muted" },
          "Record the human rework event when an earlier reading is revised. " +
          "The note is the record; it is required."),
        field("note", correctionNote),
        h("div", { class: "toolbar" },
          h("button", { onclick: busy(recordCorrection) }, "Record operator correction")),
        correctionStatus),
      ...briefSection(brief),
    ];
  });
}

function briefSection(brief) {
  if (brief.error) return [h("h2", {}, "Mission brief"), errorBox(brief.error)];
  const mission = brief.mission || {};
  const notes = Object.entries(brief.operator_gate_notes || {});
  return [
    h("h2", {}, "Mission brief"),
    h("p", {}, h("b", {}, mission.question || mission.mission_id || "")),
    h("p", { class: "faint" }, `${mission.mission_id || ""} · ${mission.kind || ""}`),
    notes.length
      ? [h("h3", {}, "Gate notes"),
         notes.map(([code, detail]) => h("div", { class: "notice warn" },
           h("b", {}, code), h("div", {}, detail)))]
      : null,
    h("h3", {}, `Claims (${(brief.claims || []).length})`),
    table({
      columns: [
        { label: "statement", render: (c) => refLink("semantic_claim", c.claim_id,
            clip(c.statement, 110)) },
        { label: "epistemic", render: (c) => badge(c.epistemic_state) },
        { label: "id", render: (c) => h("span", { class: "mono" }, c.claim_id) },
      ],
      rows: brief.claims || [], empty: "no claims visible to this actor",
    }),
    h("h3", {}, `Objectives (${(brief.objectives || []).length})`),
    table({
      columns: [
        { label: "objective", render: (o) => refLink("mission_objective", o.objective_id,
            clip(o.statement, 110)) },
        { label: "id", render: (o) => h("span", { class: "mono" }, o.objective_id) },
      ],
      rows: brief.objectives || [], empty: "no objectives visible",
    }),
    h("h3", {}, `Evidence (${(brief.evidence || []).length})`),
    table({
      columns: [
        { label: "manifestation", render: (e) => refLink("fabric_manifestation",
            e.manifestation_id, clip(e.url || e.manifestation_id, 80)) },
        { label: "source", key: "source_id" },
        { label: "retrieved", render: (e) => fmtTime(e.retrieval_time) },
      ],
      rows: brief.evidence || [], empty: "no evidence visible",
    }),
  ];
}
