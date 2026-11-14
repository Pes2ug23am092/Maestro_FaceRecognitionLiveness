import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { loadVectorResults } from "./vector-suite.js";

const results = loadVectorResults();
const failures = results.filter(
  ({ expected, actual }) => expected.outcome !== actual.outcome || expected.reason !== actual.reason,
);
const summary = { total: results.length, passed: results.length - failures.length, failures };
const outputIndex = process.argv.indexOf("--output");
if (outputIndex >= 0 && process.argv[outputIndex + 1]) {
  const outputPath = resolve(process.cwd(), process.argv[outputIndex + 1]!);
  mkdirSync(dirname(outputPath), { recursive: true });
  writeFileSync(outputPath, `${JSON.stringify(summary, null, 2)}\n`, "utf8");
  console.log(`Policy vectors: ${summary.passed}/${summary.total}; output ${outputPath}`);
} else {
  console.log(JSON.stringify(summary, null, 2));
}
if (failures.length > 0) process.exitCode = 1;