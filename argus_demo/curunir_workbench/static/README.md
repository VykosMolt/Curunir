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

## Known state — read before working here

The nav is still a family browser over stored record types rather than a
mission workflow, and the V6.8 pilots completed their real work through `curl`.
The diagnosis, the pilot evidence, and three competing redesign options are in
`../../../curunir_v68_runs/CURUNIR_WORKBENCH_UI_FABLE_BRIEF.md`. `/claims` is
the first surface built to that brief; the rest of the information
architecture is not done.

One defect named there is still in this directory: `views3.js` falls back to an
**unsigned** `POST /approve` when `ed25519Available()` is false, silently. V6.8
does not accept an unsigned approval.
