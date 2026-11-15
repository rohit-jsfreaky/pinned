// Copies the real numbers the site shows out of ../evidence into data/evidence.json (run before dev/build).
// Every number on the site comes from here; nothing is typed in by hand.
import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "evidence");
const read = (p) => (existsSync(join(root, p)) ? JSON.parse(readFileSync(join(root, p), "utf8")) : null);
const latest = (dir) => {
  if (!existsSync(join(root, dir))) return null;
  const files = readdirSync(join(root, dir)).filter((f) => f.endsWith(".json")).sort();
  return files.length ? { file: `evidence/${dir}/${files.at(-1)}`, data: read(join(dir, files.at(-1))) } : null;
};

const curve = read("curve.json");
const split = read("split.json");
const search = read("search/latency.json");
const ingest = read("ingest/summary.json");
const lat = (rows, c) => rows?.find((r) => r.concurrency === c) ?? null;

// Wrong-version API errors on the TEST split, per arm and iteration, counted from every run's results.jsonl.
const API_ERRORS = new Set(["AttributeError", "ImportError", "ModuleNotFoundError", "TypeError"]);
const apiErrors = {};
if (existsSync(join(root, "runs"))) {
  for (const run of readdirSync(join(root, "runs"))) {
    const f = join(root, "runs", run, "results.jsonl");
    if (!existsSync(f)) continue;
    for (const line of readFileSync(f, "utf8").split("\n").filter(Boolean)) {
      const r = JSON.parse(line);
      if (r.split !== "test") continue;
      const k = `${r.arm}:${r.iteration}`;
      apiErrors[k] ??= { n: 0, api_errors: 0 };
      apiErrors[k].n += 1;
      if (!r.passed && API_ERRORS.has(r.error_type)) apiErrors[k].api_errors += 1;
    }
  }
}

const out = {
  curve: curve ? { metric: curve.metric, baselines: curve.baselines ?? {}, curves: curve.curves ?? [] } : null,
  split: split ? { train: split.train.length, test: split.test.length } : null,
  search: search
    ? {
        file: "evidence/search/latency.json",
        context_for: { c1: lat(search.latency?.context_for, 1), c8: lat(search.latency?.context_for, 8), c32: lat(search.latency?.context_for, 32) },
        check_calls: { c1: lat(search.latency?.check_calls, 1), c8: lat(search.latency?.check_calls, 8) },
        graph_queries: search.graph_queries_per_call ?? null,
      }
    : null,
  api_errors: apiErrors,
  load: latest("load"),
  ingest: ingest ?? null,
};
mkdirSync(join(process.cwd(), "data"), { recursive: true });
writeFileSync(join(process.cwd(), "data", "evidence.json"), JSON.stringify(out, null, 2));
console.log("evidence synced:", Object.entries(out).map(([k, v]) => `${k}=${v ? "yes" : "no"}`).join(" "));
