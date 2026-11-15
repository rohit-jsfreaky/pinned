"use client";
import { useEffect, useState } from "react";
import { motion } from "motion/react";
import { ArrowRight, CheckCircle, Warning, Sparkle } from "@phosphor-icons/react";
import { LIBRARIES } from "@/lib/examples";
import { Badge, Eyebrow } from "./ui";

function Tile({ title, children, className = "" }: { title: string; children: React.ReactNode; className?: string }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 16 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, margin: "-60px" }}
      transition={{ duration: 0.5 }}
      className={`flex flex-col justify-between overflow-hidden rounded-2xl bg-hp-field/60 p-6 shadow-hp-plate ${className}`}
    >
      <div className="relative flex flex-1 items-center justify-center">{children}</div>
      <div className="mt-6 text-sm font-medium text-hp-ink">{title}</div>
    </motion.div>
  );
}

const ASK = "How do I add a row to a DataFrame?";
function AskBox() {
  const [n, setN] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setN((x) => (x > ASK.length + 25 ? 0 : x + 1)), 70);
    return () => clearInterval(t);
  }, []);
  const done = n >= ASK.length;
  return (
    <div className="w-full max-w-md">
      <div className="flex items-center gap-2 rounded-xl bg-hp-raised px-4 py-3 shadow-hp-float">
        <Sparkle weight="fill" className="text-hp-accent" />
        <span className="truncate text-sm text-hp-ink">{ASK.slice(0, n)}<span className="animate-caret">|</span></span>
        <span className="ml-auto rounded bg-hp-field px-1.5 font-mono text-[11px] text-hp-muted">pandas 2.0.3</span>
      </div>
      <motion.div animate={{ opacity: done ? 1 : 0, y: done ? 0 : 6 }} className="mt-2.5 rounded-xl bg-hp-raised px-4 py-3 text-sm shadow-hp-lift">
        <div className="font-mono text-[12.5px] text-hp-ink">pd.concat([df, pd.DataFrame([row])])</div>
        <div className="mt-1 text-xs text-hp-muted">DataFrame.append is gone in 2.0 — this is the 2.0 way.</div>
      </motion.div>
    </div>
  );
}

const TIMELINE = [
  ["1.3", "ok"], ["1.4", "warn"], ["1.5", "warn"], ["2.0", "bad"], ["2.1", "bad"], ["2.2", "bad"],
] as const;

export function Bento() {
  return (
    <section className="page-measure section-y">
      <div className="flex items-end justify-between gap-6 max-tablet:flex-col max-tablet:items-start">
        <div>
          <Eyebrow>Product</Eyebrow>
          <h2 className="font-display mt-3 text-5xl leading-[1.08] text-hp-ink max-tablet:text-3xl">
            Every version of every library.
            <br />
            <span className="text-hp-muted">One question your agent can ask.</span>
          </h2>
        </div>
        <a href="/try" className="inline-flex h-10 items-center gap-2 rounded-lg bg-hp-ink px-4 text-sm font-medium text-hp-on-ink hover:bg-white">
          Try it now <ArrowRight weight="bold" />
        </a>
      </div>

      <div className="mt-12 grid grid-cols-3 gap-3 max-tablet:grid-cols-1">
        <Tile title="Ask how, for your version" className="col-span-2 min-h-[300px] max-tablet:col-span-1">
          <AskBox />
        </Tile>
        <Tile title="Check code before it runs" className="min-h-[300px]">
          <div className="flex w-full flex-col gap-2">
            {[
              ["np.float", "bad", "Removed in 1.24"],
              ["np.asarray", "ok", "Valid"],
              ["np.round_", "warn", "Deprecated"],
            ].map(([c, t, l], n) => (
              <motion.div
                key={c}
                initial={{ opacity: 0, x: -10 }}
                whileInView={{ opacity: 1, x: 0 }}
                viewport={{ once: true }}
                transition={{ delay: 0.2 + n * 0.15 }}
                className="flex items-center justify-between rounded-lg bg-hp-raised px-3 py-2.5 shadow-hp-lift"
              >
                <span className="font-mono text-[12.5px] text-hp-ink">{c}</span>
                <Badge tone={t as "ok" | "bad" | "warn"}>
                  {t === "ok" ? <CheckCircle weight="fill" /> : <Warning weight="fill" />} {l}
                </Badge>
              </motion.div>
            ))}
          </div>
        </Tile>
        <Tile title="See an API across every release" className="min-h-[260px]">
          <div className="w-full">
            <div className="mb-3 font-mono text-[12.5px] text-hp-ink">DataFrame.append</div>
            <div className="relative flex items-center justify-between">
              <div className="absolute inset-x-2 top-1/2 h-px bg-hp-line" />
              {TIMELINE.map(([v, t], n) => (
                <motion.div
                  key={v}
                  initial={{ scale: 0 }}
                  whileInView={{ scale: 1 }}
                  viewport={{ once: true }}
                  transition={{ delay: 0.1 * n, type: "spring" }}
                  className="relative flex flex-col items-center gap-2"
                >
                  <span className={`size-3 rounded-full ring-4 ring-hp-field ${t === "ok" ? "bg-ok" : t === "warn" ? "bg-warn" : "bg-bad"}`} />
                  <span className="font-mono text-[11px] text-hp-muted">{v}</span>
                </motion.div>
              ))}
            </div>
            <div className="mt-4 flex gap-3 text-[11px] text-hp-muted">
              <span className="flex items-center gap-1"><i className="size-2 rounded-full bg-ok" /> valid</span>
              <span className="flex items-center gap-1"><i className="size-2 rounded-full bg-warn" /> deprecated</span>
              <span className="flex items-center gap-1"><i className="size-2 rounded-full bg-bad" /> removed</span>
            </div>
          </div>
        </Tile>
        <Tile title="Learns from every run" className="min-h-[260px]">
          <svg viewBox="0 0 240 110" className="w-full max-w-[260px]" aria-hidden>
            <motion.path
              d="M4 96 C 50 92, 70 80, 100 70 S 160 40, 236 14"
              fill="none"
              stroke="var(--color-hp-accent)"
              strokeWidth="2.5"
              strokeLinecap="round"
              initial={{ pathLength: 0 }}
              whileInView={{ pathLength: 1 }}
              viewport={{ once: true }}
              transition={{ duration: 1.6, ease: "easeInOut" }}
            />
            {[100, 160, 236].map((x, n) => (
              <motion.circle key={x} cx={x} cy={[70, 40, 14][n]} r="4" fill="var(--color-hp-page)" stroke="var(--color-hp-accent)" strokeWidth="2"
                initial={{ opacity: 0 }} whileInView={{ opacity: 1 }} viewport={{ once: true }} transition={{ delay: 0.6 + n * 0.4 }} />
            ))}
          </svg>
          <div className="absolute right-0 bottom-0 rounded-md bg-hp-raised px-2 py-1 text-[11px] text-hp-body shadow-hp-lift">every pass and fail teaches it</div>
        </Tile>
        <Tile title={`${LIBRARIES.length} libraries, every release in between`} className="min-h-[260px]">
          <div className="flex w-full min-w-0 flex-wrap justify-center gap-1.5">
            {LIBRARIES.slice(0, 18).map((l, n) => (
              <motion.span
                key={l}
                initial={{ opacity: 0, y: 6 }}
                whileInView={{ opacity: 1, y: 0 }}
                viewport={{ once: true }}
                transition={{ delay: n * 0.03 }}
                className="rounded-md bg-hp-raised px-2 py-1 font-mono text-[11.5px] text-hp-body shadow-hp-lift"
              >
                {l}
              </motion.span>
            ))}
            <span className="rounded-md px-2 py-1 font-mono text-[11.5px] text-hp-muted">+{LIBRARIES.length - 18} more</span>
          </div>
        </Tile>
      </div>
    </section>
  );
}
