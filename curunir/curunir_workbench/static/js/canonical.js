// Canonical JSON bytes for signing, matching the server byte for byte.
// Values that JavaScript and Python could serialize differently are refused.

function codePointCompare(a, b) {
  const left = Array.from(a), right = Array.from(b);
  const length = Math.min(left.length, right.length);
  for (let index = 0; index < length; index += 1) {
    const delta = left[index].codePointAt(0) - right[index].codePointAt(0);
    if (delta !== 0) return delta;
  }
  return left.length - right.length;
}

function wellFormed(value) {
  if (typeof value.isWellFormed === "function") return value.isWellFormed();
  return !/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/.test(value);
}

function canonical(value) {
  if (value === null) return "null";
  const kind = typeof value;
  if (kind === "boolean") return value ? "true" : "false";
  if (kind === "number") {
    if (!Number.isInteger(value) || !Number.isSafeInteger(value) ||
        Object.is(value, -0)) {
      throw new Error("canonical: signed numbers must be safe integers");
    }
    return String(value);
  }
  if (kind === "string") {
    if (!wellFormed(value)) {
      throw new Error("canonical: lone surrogate is not valid interchange Unicode");
    }
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) {
    return "[" + value.map(canonical).join(",") + "]";
  }
  if (kind === "object") {
    const parts = [];
    for (const key of Object.keys(value).sort(codePointCompare)) {
      if (value[key] === undefined) {
        throw new Error("canonical: undefined values are not supported");
      }
      parts.push(canonical(key) + ":" + canonical(value[key]));
    }
    return "{" + parts.join(",") + "}";
  }
  throw new Error("canonical: unsupported value type " + kind);
}

function canonicalBytes(value) {
  return new TextEncoder().encode(canonical(value));
}

export { canonical, canonicalBytes };
