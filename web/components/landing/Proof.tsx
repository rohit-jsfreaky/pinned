"use client";
import { motion } from "motion/react";
import { evidence, pct } from "@/lib/evidence";
import { Eyebrow } from "./ui";

const W = 1000, H = 300, PAD = 28;
const LO = 0.5, HI = 1.0;
const y = (v: number) => H - PAD - ((v - LO) / (HI - LO)) * (H - 2 * PAD);

export function Proof() {
  const base = evidence.curve?.baselines ?? {};
  const curves = (evidence.curve?.curves ?? []).filter((c) => (c.points ?? []).length);
  const main = curves.find((c) => /graph|pinned/i.test(c.arm ?? "")) ?? curves[0];
  const pts = main?.points ?? [];
  const maxIt = Math.max(1, ...pts.map((p) => p.iteration));
  const x = (it: number) => PAD + (it / maxIt) * (W - 2 * PAD);
  const path = pts.map((p, n) => `${n ? "L" : "M"}${x(p.iteration)},${y(p.pass_at_1)}`).join(" ");
  const best = pts.length ? pts.reduce((a, b) => (b.pass_at_1 > a.pass_at_1 ? b : a)) : null;
  const c1 = evidence.search?.context_for.c1;
  const k1 = evidence.search?.check_calls.c1;
  const q = evidence.search?.graph_queries?.context_for_warm?.p50;

  const stats: [string, string][] = [
    ...(base.none ? [[pct(base.none.pass_at_1), "agent alone"] as [string, string]] : []),
    ...(base.docs ? [[pct(base.docs.pass_at_1), "agent + official docs"] as [string, string]] : []),
    ...(best ? [[pct(best.pass_at_1), "agent + Pinned"] as [string, string]] : []),
    ...(c1 ? [[`${Math.round(c1.p50_ms)} ms`, "to answer how-to, median"] as [string, string]] : []),
    ...(k1 ? [[`${Math.round(k1.p50_ms)} ms`, "to check code, median"] as [string, string]] : []),
    ...(q ? [[`${q}`, "graph hops per answer"] as [string, string]] : []),
  ];

  return (
    <section id="proof" className="relative section-y scroll-mt-16 overflow-hidden">
      <div className="page-measure relative">
        <Eyebrow>Proof</Eyebrow>
        <h2 className="font-display mt-3 max-w-2xl text-5xl leading-[1.08] text-hp-ink max-tablet:text-3xl">
          Measured, not promised.
          <br />
          <span className="text-hp-muted">On {(evidence.split?.test ?? 0) + (evidence.split?.train ?? 0)} real version-pinned coding tasks.</span>
        </h2>
        <p className="mt-4 max-w-xl text-lg text-hp-muted">
          Each task pins a real library release and runs hidden tests. Pinned learns only from the practice half, and every
          number here is scored on the {evidence.split?.test} tasks it has never seen.
        </p>
        <div className="mt-12 grid grid-cols-3 gap-x-10 gap-y-8 max-tablet:grid-cols-2">
          {stats.map(([v, l], n) => (
            <motion.div
              key={l}
              initial={{ opacity: 0, y: 10 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={{ delay: n * 0.08 }}
              className="border-l-2 border-hp-line pl-4"
            >
              <div className="font-display text-[34px] leading-none text-hp-ink tabular-nums">{v}</div>
              <div className="mt-2 text-sm text-hp-muted">{l}</div>
            </motion.div>
          ))}
        </div>

        <div className="mt-16 rounded-2xl bg-hp-field/50 p-6 shadow-hp-plate max-tablet:p-3">
          <div className="mb-3 flex flex-wrap gap-5 text-[12px] text-hp-body">
            <span className="flex items-center gap-2"><i className="h-0.5 w-5 bg-hp-accent" /> Pinned, by learning round</span>
            <span className="flex items-center gap-2"><i className="h-0 w-5 border-t-2 border-dashed border-hp-muted" /> agent + docs</span>
            <span className="flex items-center gap-2"><i className="h-0 w-5 border-t-2 border-dotted border-hp-muted/70" /> agent alone</span>
          </div>
          <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label="pass rate by learning round">
            {[0.5, 0.6, 0.7, 0.8, 0.9, 1].map((v) => (
              <g key={v}>
                <line x1={PAD} x2={W - PAD} y1={y(v)} y2={y(v)} stroke="var(--color-hp-line)" />
                <text x={4} y={y(v) + 4} fontSize="11" fill="var(--color-hp-muted)">{Math.round(v * 100)}</text>
              </g>
            ))}
            {base.docs && <line x1={PAD} x2={W - PAD} y1={y(base.docs.pass_at_1)} y2={y(base.docs.pass_at_1)} stroke="var(--color-hp-muted)" strokeWidth="2" strokeDasharray="8 6" />}
            {base.none && <line x1={PAD} x2={W - PAD} y1={y(base.none.pass_at_1)} y2={y(base.none.pass_at_1)} stroke="var(--color-hp-muted)" strokeOpacity=".7" strokeWidth="2" strokeDasharray="2 5" />}
            {path && (
              <motion.path d={path} fill="none" stroke="var(--color-hp-accent)" strokeWidth="3" strokeLinejoin="round"
                initial={{ pathLength: 0 }} whileInView={{ pathLength: 1 }} viewport={{ once: true }} transition={{ duration: 1.8, ease: "easeInOut" }} />
            )}
            {pts.map((p) => (
              <circle key={p.iteration} cx={x(p.iteration)} cy={y(p.pass_at_1)} r="5" fill="var(--color-hp-page)" stroke="var(--color-hp-accent)" strokeWidth="2.5" />
            ))}
            {!pts.length && (
              <text x={W / 2} y={y(0.9)} textAnchor="middle" fontSize="14" fill="var(--color-hp-muted)">Pinned learning rounds are being measured</text>
            )}
          </svg>
          <div className="mt-2 text-right font-mono text-[11px] text-hp-muted">{evidence.curve?.metric}</div>
        </div>
      </div>
    </section>
  );
}
