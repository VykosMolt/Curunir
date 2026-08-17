// Byte-exact canonical JSON serialization — the browser half of the signing
// contract. It MUST produce the identical bytes to the server's
// curunir_operational.canonical.canonical_line (Python
// json.dumps(_normal(v), sort_keys=True, separators=(",",":"), ensure_ascii=False)),
// because the browser signs canonical(payload) and the server verifies the
// signature by re-canonicalizing the SAME payload object. A one-byte divergence
// makes every signature fail. Proven equal to the Python serializer by
// tests/test_workbench_canonical_js_parity.py over an adversarial corpus.
//
// This is deliberately stricter than JSON.stringify: it sorts object keys
// (JSON.stringify preserves insertion order), and it THROWS on values whose
// canonical form could differ between JS and Python (floats, non-finite
// numbers, unsafe integers) or that JSON.stringify would silently drop
// (undefined) — so the browser never signs a payload it cannot serialize
// identically to the verifier.

// Compare two strings by Unicode code point (Array.from iterates code points,
// surrogate-pair-aware), matching Python's string ordering used by sort_keys.
function codePointCompare(a, b) {
  const ca = Array.from(a), cb = Array.from(b);
  const n = Math.min(ca.length, cb.length);
  for (let i = 0; i < n; i++) {
    const d = ca[i].codePointAt(0) - cb[i].codePointAt(0);
    if (d !== 0) return d;
  }
  return ca.length - cb.length;
}

function canonical(value) {
  if (value === null) return "null";
  const t = typeof value;
  if (t === "boolean") return value ? "true" : "false";
  if (t === "number") {
    // Only safe integers: Python (arbitrary-precision int) and JS (f64) print
    // these identically. Floats and huge/again non-finite numbers can diverge
    // (e.g. Python 1e-07 vs JS 1e-7), so refuse them rather than sign a payload
    // whose bytes the verifier would compute differently.
    if (!Number.isInteger(value) || !Number.isSafeInteger(value)) {
      throw new Error(
        "canonical: only safe-integer numbers may appear in a signed payload " +
        "(got " + value + "); carry non-integers as strings");
    }
    return String(value);
  }
  if (t === "string") {
    // JSON.stringify of a lone string matches Python json.dumps(ensure_ascii=
    // False) escaping for every well-formed (no lone-surrogate) string:
    // quotes/backslash, the short escapes \b \t \n \f \r, other C0 controls as
    // \u00xx, everything else (incl. all non-ASCII) literal.
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) {
    return "[" + value.map(canonical).join(",") + "]";
  }
  if (t === "object") {
    // Sort keys by Unicode CODE POINT, to match Python's sort_keys (which
    // compares strings by code point). The default JS .sort() compares UTF-16
    // code UNITS, which diverges for astral-plane (>= U+10000) keys — harmless
    // for today's fixed-ASCII signed payloads, but a latent trap for any future
    // signed command carrying a data-keyed map (review finding F7).
    const keys = Object.keys(value).sort(codePointCompare);
    const parts = [];
    for (const k of keys) {
      const v = value[k];
      if (v === undefined) {
        throw new Error("canonical: undefined value at key " + JSON.stringify(k) +
                        " (JSON.stringify would silently drop it)");
      }
      parts.push(JSON.stringify(k) + ":" + canonical(v));
    }
    return "{" + parts.join(",") + "}";
  }
  throw new Error("canonical: unsupported value type " + t);
}

// The exact bytes signed/verified (UTF-8 of the canonical string).
function canonicalBytes(value) {
  return new TextEncoder().encode(canonical(value));
}

export { canonical, canonicalBytes };
