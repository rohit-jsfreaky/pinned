"use client";
import { useState } from "react";
import { motion } from "motion/react";
import { CheckCircle, Warning, ArrowBendDownRight, Clock, TextAa, Trash } from "@phosphor-icons/react";
import { Badge, Eyebrow } from "./ui";

const CARDS = [
  { path: "pandas.DataFrame.append", sub: "what your agent wrote", badge: <Badge tone="bad"><Warning weight="fill" /> Removed in 2.0.0</Badge> },
  { path: "pandas.concat", sub: "replaced by · the pandas 2.0 path", badge: <Badge tone="ok"><CheckCircle weight="fill" /> Valid in 2.0.3</Badge> },
  { path: "pandas.concat", sub: "signature in 2.0.3", badge: <Badge tone="neutral">ready to use</Badge>, detail: true },
];

const KINDS = [
  { icon: Trash, t: "Removed calls", d: "Calls that no longer exist in your version, with the one that replaced them." },
  { icon: TextAa, t: "Changed arguments", d: "Renamed or dropped keywords, caught before the code ever runs." },
  { icon: Clock, t: "Added later", d: "Calls from newer releases than yours, and what to use until you upgrade." },
];

export function Chain() {
  const [kind, setKind] = useState(0);
  return (
    <section className="section-y">
      <div className="page-measure border-x border-t border-hp-line">
        <div className="flex flex-col px-10 pt-20 max-tablet:px-5 max-tablet:pt-12">
          <Eyebrow>The receipt</Eyebrow>
          <h2 className="font-display mt-3 max-w-xl text-pretty text-5xl text-hp-ink max-tablet:text-3xl">Every answer shows its proof</h2>
          <p className="mt-3 max-w-xl text-pretty text-lg text-hp-muted">
            Pinned never just says “use this”. It shows the exact path from the call you wrote to the call that works on your version.
          </p>
        </div>
        <div className="mt-16 border-y border-hp-line bg-hp-field/50">
          <div className="flex h-[420px] items-center justify-center overflow-hidden px-8 max-tablet:h-[360px] max-tablet:px-3">
            <div className="w-full max-w-2xl [mask-image:linear-gradient(black_78%,transparent)]">
              <div className="flex flex-col gap-2.5">
                {CARDS.map((c, idx) => (
                  <motion.div
                    key={idx}
                    initial={{ opacity: 0, x: 20 }}
                    whileInView={{ opacity: 1, x: 0 }}
                    viewport={{ once: true, margin: "-120px" }}
                    transition={{ delay: 0.1 + idx * 0.15, duration: 0.5 }}
                    style={{ marginLeft: `${idx * 8}%` }}
                    className="cursor-default rounded-xl border border-hp-line shadow-sm transition-transform duration-300 hover:-translate-x-3"
                  >
                    <div className="flex items-center justify-between gap-8 rounded-[inherit] bg-hp-raised px-4 py-5 max-tablet:gap-3">
                      <div className="flex min-w-0 items-center gap-3">
                        <div className="flex-none rounded-full border border-hp-line bg-gradient-to-t from-hp-field p-2 text-hp-body">
                          {idx === 0 ? <Warning size={16} weight="bold" /> : <ArrowBendDownRight size={16} weight="bold" />}
                        </div>
                        <div className="flex min-w-0 flex-col text-sm">
                          <span className="truncate font-mono font-medium text-hp-ink">{c.path}</span>
                          <span className="truncate text-hp-muted">{c.sub}</span>
                        </div>
                      </div>
                      {c.badge}
                    </div>
                    {c.detail && (
                      <div className="rounded-b-xl border-t border-hp-line bg-hp-raised px-5 pt-3 pb-6 font-mono text-[12.5px] leading-6 text-hp-body">
                        <div className="mb-2 flex gap-4 font-sans text-xs text-hp-muted">
                          <span className="border-b border-hp-ink pb-1 text-hp-ink">Signature</span>
                          <span>Timeline</span>
                        </div>
                        concat(objs, *, axis=0, join=&apos;outer&apos;, ignore_index=False, keys=None, levels=None, names=None,
                        verify_integrity=False, sort=False, copy=None)
                      </div>
                    )}
                  </motion.div>
                ))}
              </div>
            </div>
          </div>
          <div className="grid grid-cols-3 border-t border-hp-line max-tablet:grid-cols-1">
            {KINDS.map((k, n) => (
              <button
                key={k.t}
                onClick={() => setKind(n)}
                className={`relative px-10 py-8 text-left transition-colors max-tablet:px-5 ${n ? "border-l border-hp-line max-tablet:border-l-0 max-tablet:border-t" : ""}`}
              >
                <span className={`absolute left-0 top-8 h-6 w-[2px] transition-colors ${n === kind ? "bg-hp-accent" : "bg-transparent"}`} />
                <k.icon size={18} weight="bold" className={n === kind ? "text-hp-ink" : "text-hp-muted"} />
                <div className={`mt-3 font-medium ${n === kind ? "text-hp-ink" : "text-hp-muted"}`}>{k.t}</div>
                <p className={`mt-2 mb-0 text-sm ${n === kind ? "text-hp-body" : "text-hp-muted/70"}`}>{k.d}</p>
              </button>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}
