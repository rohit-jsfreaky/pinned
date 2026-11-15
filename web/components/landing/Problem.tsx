"use client";
import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { CheckCircle, Warning, Robot, PushPin } from "@phosphor-icons/react";
import { evidence } from "@/lib/evidence";

const none = evidence.curve?.baselines?.none;

const STEPS = [
  { t: "Stop guessing which version the docs were for", d: "Agents mix examples from five releases. Most of them were never yours." },
  { t: "Check every call against the versions you run", d: "Each call is looked up for your exact library version, before the code runs." },
  { t: "Get the call that works, with the proof", d: "When an API moved, Pinned hands over the replacement and shows why." },
];

const LOG = [
  { who: "agent", icon: Robot, text: "Writing report.py for pandas 2.0.3…", tone: "body" },
  { who: "run", icon: Warning, text: "AttributeError: 'DataFrame' object has no attribute 'append'", tone: "bad" },
  { who: "pinned", icon: PushPin, text: "DataFrame.append was removed in pandas 2.0. Use pandas.concat.", tone: "ok" },
  { who: "agent", icon: Robot, text: "Rewrote add_row with pd.concat.", tone: "body" },
  { who: "run", icon: CheckCircle, text: "12 tests passed", tone: "ok" },
] as const;

export function Problem() {
  const [active, setActive] = useState(0);
  const [tick, setTick] = useState(0);
  const shown = Math.min(LOG.length, (tick % (LOG.length + 3)) + 1); // fill up, hold the full log, repeat
  useEffect(() => {
    const a = setInterval(() => setActive((x) => (x + 1) % STEPS.length), 3200);
    const b = setInterval(() => setTick((x) => x + 1), 1100);
    return () => { clearInterval(a); clearInterval(b); };
  }, []);
  return (
    <section className="page-measure section-y grid grid-cols-2 items-center gap-16 max-desktop:gap-10 max-tablet:grid-cols-1">
      <div>
        <h2 className="font-display m-0 text-[52px] leading-[1.06] text-hp-ink max-tablet:text-[36px]">
          Your agent wrote it for the wrong version.
          <br />
          <span className="text-hp-muted">Now it checks first.</span>
        </h2>
        <div className="mt-8 flex flex-col gap-2">
          {STEPS.map((s, n) => (
            <button
              key={s.t}
              onClick={() => setActive(n)}
              className={`rounded-xl px-4 py-3.5 text-left transition-all duration-300 ${n === active ? "bg-hp-raised shadow-hp-float" : "bg-hp-field/70 hover:bg-hp-field"}`}
            >
              <div className={`text-[15px] font-semibold ${n === active ? "text-hp-ink" : "text-hp-body"}`}>{s.t}</div>
              <AnimatePresence initial={false}>
                {n === active && (
                  <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} exit={{ height: 0, opacity: 0 }} className="overflow-hidden">
                    <p className="mt-1 mb-0 text-sm text-hp-muted">{s.d}</p>
                  </motion.div>
                )}
              </AnimatePresence>
            </button>
          ))}
        </div>
        {none && (
          <p className="mt-6 max-w-md text-sm text-hp-muted">
            On {none.n} held-out tasks pinned to real library versions, a strong coding model working alone failed{" "}
            {none.n - none.passed} of them.
          </p>
        )}
      </div>
      <div className="rounded-2xl bg-hp-plate p-5 shadow-hp-plate">
        <div className="overflow-hidden rounded-xl bg-hp-raised shadow-hp-window">
          <div className="flex items-center justify-between border-b border-hp-line px-4 py-3 text-sm">
            <span className="font-semibold text-hp-ink">Agent session</span>
            <span className="font-mono text-[11px] text-hp-muted">pandas==2.0.3</span>
          </div>
          <div className="flex min-h-[330px] flex-col">
            {LOG.slice(0, shown).map((l, n) => (
              <motion.div
                key={n}
                initial={{ opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.35 }}
                className="flex items-start gap-3 border-b border-hp-line/70 px-4 py-3.5 last:border-b-0"
              >
                <span className={`mt-0.5 grid size-7 shrink-0 place-items-center rounded-full ${l.tone === "bad" ? "bg-bad-soft text-bad" : l.tone === "ok" ? "bg-ok-soft text-ok" : "bg-hp-field text-hp-body"}`}>
                  <l.icon weight="fill" size={14} />
                </span>
                <div className="min-w-0">
                  <div className="text-[12px] font-semibold uppercase tracking-[0.06em] text-hp-muted">{l.who}</div>
                  <div className={`text-[14px] ${l.tone === "bad" ? "font-mono text-[12.5px] text-bad" : "text-hp-ink"}`}>{l.text}</div>
                </div>
              </motion.div>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}
