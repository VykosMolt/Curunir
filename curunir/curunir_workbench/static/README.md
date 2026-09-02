# Static — the analyst SPA

A hash-router single-page app over the HTTP command surface. No build step, no
framework, no bundler: plain ES modules the server ships as-is.

| File | Role |
|---|---|
| `index.html` | shell |
| `app.css` | dark, dense COP styling |
| `js/app.js` | router and shell |
| `js/api.js` | fetch wrapper |
| `js/session.js` | current actor |
| `js/identity.js` | **WebCrypto Ed25519 signing**, non-extractable key in IndexedDB |
| `js/canonical.js` | canonical JSON, byte-exact with the Python serializer |
| `js/ui.js` | shared primitives; epistemic labels stay text-first, colour never replaces meaning |
| `js/views*.js` | the family views |
| `js/pilot.js` | V6.8 pilot session control + mission brief (only when the server has `/v68/pilot/*`) |

`canonical.js` is load-bearing and non-obvious: the browser must produce **the
same bytes** the server hashes, or a signature over a report version will not
verify. `tests/js/canonical_harness.mjs` proves parity against Python.

## Design: "Quiet Instrument"

An editorial reading surface with operator density underneath. Three rules
`app.css` exists to enforce:

1. **The value is the largest thing on screen.** `Apple Inc.` is 26px; the
   predicate above it is an 11px label and the provenance below it is a
   whisper. What the evidence says is the headline — not the field name, and
   never the identifier.
2. **Epistemic status is a word.** `unreviewed`, `superseded`, `contested`.
   Colour and left-rule weight reinforce; they never carry meaning alone, so a
   monochrome screenshot loses nothing. Independent basis is drawn as pips
   (`◆◇◇`), which survive with no colour at all.
3. **Identifiers are not the operator's vocabulary.** Raw ids and hashes are
   wrapped in `<code class="ident">` and revealed only in **Auditor mode**
   (press `a`). They are always in the DOM — custody is real and inspectable —
   but they are not what a person reads to do the job.

Press `t` to cycle dark / light / system. Both themes are first-class. There are
no webfonts: this tool makes no outbound request for typography.

`refLink(kind, id, label)` is the seam that used to leak. Without a label it
now renders a short handle (`claim ·1ec902`) plus an auditor-only full id,
instead of a 28-character hash.

## Operating the V6.8 pilot from here

Every step of `../../CURUNIR_V6_8_PILOT_PROTOCOL.md` that used to need `curl`
is now a control on a page:

* **sign-in** — on a pilot server (the unauthenticated probe of
  `/v68/pilot/status` answers 401; the plain workbench 404) the form offers a
  participant role and note; with a role chosen the session starts before the
  first authenticated read, so nothing falls outside the session the harness
  bounds actions to.
* **`/pilot`** — end session, operator correction, and the frozen
  `/v68/pilot/brief` (question, gate notes, claims, objectives, evidence).
  Ending a session renders a static notice and makes no further read. The nav
  group appears only when the pilot routes exist.
* **`/forecasts`** — "Author forecast (human judgment)": the initial authorship
  the protocol's M1 step 4 did over HTTP, with propositions chosen from visible
  claims.
* **`/reports`** — a compartment multi-select and a role floor on dossier
  creation (M3 step 2), offered from what `/api/session` reports for the
  signed-in actor; a dossier is created at the floor its basis needs, since an
  edit never raises it.
* the sentence editor — "cite claim" / "cite assumption" pickers over visible
  records; the raw comma-separated fields stay editable.
* review dispositions, report reject/return notes, task closing notes, route
  assignment, saved-view titles and annotation resolutions are inline fields,
  and dissent is acknowledged with a checkbox: no `prompt()`, `confirm()` or
  `alert()`, so a headless browser can drive the whole protocol.

`api.js` sends `X-Curunir-Client: workbench-ui` on every request. The pilot
harness records the value each request claimed on its `HTTP_ACTION` and counts
mutations by it. The header is unauthenticated, so a script that sets it is
counted as the UI: the count describes how the operator worked and proves
nothing on its own.

## Known state — read before working here

The nav is still a family browser over stored record types rather than a
mission workflow. The diagnosis, the pilot evidence, and three competing
redesign options are in
`../../../curunir_v68_runs/CURUNIR_WORKBENCH_UI_FABLE_BRIEF.md`. `/claims` is
the first surface built to that brief; the rest of the information
architecture is not done.
