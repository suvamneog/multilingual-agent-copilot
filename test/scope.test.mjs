/* Gate 1 (scope) and gate 2 (real need) tests.   node test/scope.test.mjs
 * Why: "car policy konsa accha hai" once ranked health plans as "Best fit" for a healthy customer. */
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const ctx = { window: {} };
ctx.globalThis = ctx;
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(path.join(here, "..", "web", "agent-core.js"), "utf8"), ctx);
const C = Object.values(ctx.window).concat(Object.values(ctx)).find((v) => v && v.outOfScope);
if (!C) throw new Error("agent-core.js did not expose outOfScope");

const SCOPE = [
  ["car policy konsa accha hai", "motor"], ["bike insurance batao", "motor"], ["gaadi ka bima chahiye", "motor"],
  ["What is the IDV of my car?", "motor"], ["term plan chahiye papa ke liye", "life"], ["best life insurance?", "life"],
  ["LIC jeevan anand", "life"], ["travel insurance for Dubai trip", "travel"], ["home insurance fire cover", "home"],
  /* health questions that mention a car or a word like "term" must stay in scope */
  ["car accident mein hospital bill cover hai?", null], ["Is ambulance after a bike accident covered?", null],
  ["Care Supreme", null], ["Is cataract covered?", null], ["policy term kitne saal ka hai?", null],
  ["mera blood pressure high hai, konsa plan acha hai", null], ["Star Health Cardiac Care", null],
];
const NEED = [  /* [text, isNeed with no product picked] */
  ["konsa plan accha hai", false], ["best health plan batao", false], ["which policy is good?", false],
  ["mera blood pressure high hai, konsa plan acha hai", true], ["mother 58 diabetes", true],
  ["papa 66 saal ke hain, diabetes nahi hai", true], ["My wife is 28. We are planning a baby next year.", true],
  ["I am 30, no disease, I want a cheap plan", true],
];
let fail = 0, n = 0;
for (const [t, want] of SCOPE) {
  n++; const got = C.outOfScope(t);
  if (got !== want) { fail++; console.log(`FAIL scope  "${t}": got ${got}, want ${want}`); }
}
for (const [t, want] of NEED) {
  n++; const got = C.isNeed(C.parseNeed(t), false);
  if (got !== want) { fail++; console.log(`FAIL need   "${t}": got ${got}, want ${want}`); }
}
console.log(`${n - fail}/${n} passed`);
process.exit(fail ? 1 : 0);
