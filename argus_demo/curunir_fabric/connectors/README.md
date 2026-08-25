# Connectors — the only code that talks to the outside world

One connector per source family. Each takes a typed request, goes out through
the **single guarded transport** (`../transport.py`), and returns bytes plus the
metadata needed to preserve them under custody. A connector never decides
whether a source *may* be reached — `policy` and the egress gate decide that
before it is called.

| Connector | Source | Notes |
|---|---|---|
| `gleif.py` | GLEIF LEI records | global legal-entity identity and registration status |
| `edgar.py` | SEC EDGAR | US filings; full-text search + archive documents |
| `wikidata.py` | Wikidata | entity labels, aliases, structured claims |
| `wayback.py` | Internet Archive CDX | historical captures; an archived page speaks for the archived site, not the archive |
| `rss.py` | RSS / Atom feeds | DOCTYPE-guarded against entity-expansion attacks |
| `web_page.py` | arbitrary public pages | reached only through the egress gate |
| `base.py` | — | the shared contract, and `SAFE_DEFAULT_TRANSPORT` |

**No EU-specific source is wired yet.** EUR-Lex, TED, national business
registers and the consolidated sanctions list are the obvious next connectors;
the multilingual identifiers they emit (CELEX, ELI, Official Journal numbering)
are *already* recognised downstream by
`curunir_semantic/provenance_capture.py`.

## The rules a connector must obey

- **Custody before parsing.** Bytes are hashed and preserved before anything
  interprets them.
- **A failure is a failure, never an absence.** A throttled response, an error
  page returned with HTTP 200, or a truncated body is recorded as
  `SOURCE_FAILED` — never as "this source has no data", which would silently
  become evidence of absence.
- **Hostile values are normalised once, at the edge.** Lone surrogates and
  malformed Unicode are scrubbed in `NativeResult` / `ConnectorResponse`
  construction, so nothing downstream has to defend against them.
- **Overrides are a test seam, not a second transport.** A connector may accept
  an injected transport for offline tests; production traffic has exactly one
  path out.

Tests: `tests/test_fabric_connectors.py`, `tests/test_fabric_ssrf_guard.py`.
