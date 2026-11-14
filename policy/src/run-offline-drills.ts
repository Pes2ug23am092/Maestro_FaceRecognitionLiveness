import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { evaluateOfflineDrills } from "./offline-chaos-drill.js";

const results = evaluateOfflineDrills();
const report = {
  total: results.length,
  passed: results.filter((result) => result.passed).length,
  failOpen: results.filter((result) => result.mustDeny && result.observed.startsWith("ALLOW:")).length,
  results,
};
const outputPath = resolve(process.cwd(), "../report/offline-chaos.json");
mkdirSync(resolve(process.cwd(), "../report"), { recursive: true });
writeFileSync(outputPath, `${JSON.stringify(report, null, 2)}\n`, "utf8");
console.log(JSON.stringify({ ...report, outputPath }, null, 2));
if (report.passed !== report.total) process.exitCode = 1;