// Real numbers only: data/evidence.json is generated from ../evidence by scripts/sync-evidence.mjs.
import raw from "@/data/evidence.json";

type Lat = { p50_ms: number; p95_ms: number; qps: number; errors: number } | null;
type Baseline = { n: number; passed: number; pass_at_1: number };
type CurvePoint = { iteration: number; pass_at_1: number; n?: number };

export type Evidence = {
  curve: { metric: string; baselines: Record<string, Baseline>; curves: { arm?: string; points?: CurvePoint[] }[] } | null;
  split: { train: number; test: number } | null;
  api_errors: Record<string, { n: number; api_errors: number }>;
  search: {
    context_for: { c1: Lat; c8: Lat; c32: Lat };
    check_calls: { c1: Lat; c8: Lat };
    graph_queries: { context_for_warm?: { p50: number } } | null;
  } | null;
};

export const evidence = raw as unknown as Evidence;
export const pct = (x: number) => `${(x * 100).toFixed(1)}%`;
