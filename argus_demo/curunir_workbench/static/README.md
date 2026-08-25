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

## Known state — read before working here

This UI is a family-browser over stored record types, not a mission workflow.
The V6.8 live pilots completed their actual work through `curl` and Python, not
through these screens. The diagnosis, the pilot evidence behind it, and three
competing redesign options are written up in
`../../../curunir_v68_runs/CURUNIR_WORKBENCH_UI_FABLE_BRIEF.md`. Read that
before changing anything structural.

One defect named there is in this directory: `views3.js` falls back to an
**unsigned** `POST /approve` when `ed25519Available()` is false, silently. V6.8
does not accept an unsigned approval.
