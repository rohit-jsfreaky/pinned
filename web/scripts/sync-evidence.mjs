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
  load: latest("load"),
  ingest: ingest ?? null,
};
mkdirSync(join(process.cwd(), "data"), { recursive: true });
writeFileSync(join(process.cwd(), "data", "evidence.json"), JSON.stringify(out, null, 2));
console.log("evidence synced:", Object.entries(out).map(([k, v]) => `${k}=${v ? "yes" : "no"}`).join(" "));
