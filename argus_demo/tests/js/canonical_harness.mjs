import { readFileSync } from "node:fs";
import { canonicalBytes } from "../../curunir_workbench/static/js/canonical.js";

const inputs = JSON.parse(readFileSync(process.argv[2], "utf-8"));
const output = inputs.map((item) => {
  try {
    return "OK " + Buffer.from(canonicalBytes(item)).toString("base64");
  } catch (error) {
    return "ERR " + error.message;
  }
});
process.stdout.write(output.join("\n") + "\n");
