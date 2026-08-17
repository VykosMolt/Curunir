// Differential-test harness: import the REAL shipped browser canonical
// serializer and emit base64(canonicalBytes(x)) for each input, so a Python
// test can compare it byte-for-byte against curunir_operational.canonical.
// Usage: node canonical_harness.mjs <inputs.json>
// inputs.json is a JSON array; for each element we print one line:
//   OK <base64>            (canonical bytes)
//   ERR <message>          (canonical() refused it — expected for floats etc.)
import { readFileSync } from "node:fs";
import { canonicalBytes } from "../../curunir_workbench/static/js/canonical.js";

const inputs = JSON.parse(readFileSync(process.argv[2], "utf-8"));
const out = [];
for (const item of inputs) {
  try {
    const bytes = canonicalBytes(item);
    out.push("OK " + Buffer.from(bytes).toString("base64"));
  } catch (e) {
    out.push("ERR " + e.message);
  }
}
process.stdout.write(out.join("\n") + "\n");
