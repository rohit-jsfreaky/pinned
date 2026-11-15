"use client";
import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { ArrowRight, CheckCircle, Warning, MagnifyingGlass } from "@phosphor-icons/react";
import { EXAMPLES, type Example } from "@/lib/examples";
import { Badge, MacWindow } from "./ui";

type Stage = "typing" | "scanning" | "flagged" | "fixed";
const STAGES: [Stage, number][] = [["typing", 1500], ["scanning", 900], ["flagged", 2600], ["fixed", 2200]];

function Line({ text, bad, stage }: { text: string; bad?: string; stage: Stage }) {
  if (!bad || !text.includes(bad) || stage === "typing" || stage === "fixed") return <>{text || " "}</>;
  const [a, b] = text.split(bad);
  return (
    <>
      {a}
      <span className={stage === "flagged" ? "rounded-sm bg-bad-soft text-bad underline decoration-bad decoration-wavy underline-offset-4" : ""}>{bad}</span>
      {b}
    </>
  );
}

function Editor({ ex, stage }: { ex: Example; stage: Stage }) {
  const lines = stage === "fixed" ? ex.after : ex.before;
  return (
    <div className="relative min-h-[260px] overflow-x-auto bg-hp-raised px-5 py-4 font-mono text-[13px] leading-[1.75] text-hp-body">
      <div className="mb-3 flex items-center gap-2 text-[11px] text-hp-muted">
        <span className="rounded bg-hp-field px-1.5 py-0.5">{ex.file}</span>
        <span>requirements: {ex.lib}=={ex.version}</span>
      </div>
      {lines.map((l, n) => (
        <motion.div
          key={`${stage === "fixed"}-${n}`}
          initial={stage === "typing" || stage === "fixed" ? { opacity: 0, x: -6 } : false}
          animate={{ opacity: 1, x: 0 }}
          transition={{ delay: n * 0.18, duration: 0.25 }}
          className="flex gap-4 whitespace-pre"
        >
          <span className="w-4 select-none text-right text-hp-muted/60">{n + 1}</span>
          <span className={stage === "fixed" && l !== ex.before[n] ? "rounded-sm bg-ok-soft text-ok" : ""}>
            <Line text={l} bad={ex.bad} stage={stage} />
          </span>
        </motion.div>
      ))}
      {stage === "scanning" && (
        <motion.div
          className="pointer-events-none absolute inset-x-0 h-10 bg-gradient-to-b from-transparent via-hp-accent/10 to-transparent"
          initial={{ top: 40 }}
          animate={{ top: 220 }}
          transition={{ duration: 0.9, ease: "easeInOut" }}
        />
      )}
    </div>
  );
}

function Verdict({ ex, stage }: { ex: Example; stage: Stage }) {
  return (
    <div className="flex min-h-[260px] flex-col gap-3 border-l border-hp-line bg-hp-field/50 p-4 text-sm max-tablet:border-l-0 max-tablet:border-t">
      <div className="flex items-center justify-between">
        <span className="font-semibold text-hp-ink">Pinned</span>
        <span className="font-mono text-[11px] text-hp-muted">{ex.lib} {ex.version}</span>
      </div>
      <AnimatePresence mode="wait">
        {stage === "typing" && (
          <motion.p key="wait" className="text-hp-muted" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
            Waiting for code…
          </motion.p>
        )}
        {stage === "scanning" && (
          <motion.p key="scan" className="flex items-center gap-2 text-hp-body" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
            <MagnifyingGlass weight="bold" /> Checking every call against {ex.lib} {ex.version}
          </motion.p>
        )}
        {(stage === "flagged" || stage === "fixed") && (
          <motion.div key="res" className="flex flex-col gap-2.5" initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}>
            <div className="rounded-lg bg-hp-raised p-3 shadow-hp-lift">
              <div className="flex items-center justify-between gap-2">
                <span className="truncate font-mono text-[12.5px] text-hp-ink">{ex.call}</span>
                <Badge tone="bad"><Warning weight="fill" /> Removed in {ex.removedIn}</Badge>
              </div>
            </div>
            <div className="flex items-center gap-2 pl-3 text-[12px] text-hp-muted">
              <ArrowRight weight="bold" /> replaced by
            </div>
            <div className="rounded-lg bg-hp-raised p-3 shadow-hp-lift">
              <div className="flex items-center justify-between gap-2">
                <span className="truncate font-mono text-[12.5px] text-hp-ink">{ex.fix}</span>
                <Badge tone="ok"><CheckCircle weight="fill" /> Valid in {ex.version}</Badge>
              </div>
              <div className="mt-2 truncate font-mono text-[11.5px] text-hp-muted">{ex.fixCode}</div>
            </div>
            {stage === "fixed" && (
              <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="mt-1 flex items-center gap-2 text-[13px] font-medium text-ok">
                <CheckCircle weight="fill" /> Fixed. Runs on {ex.lib} {ex.version}.
              </motion.div>
            )}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

export function ProductWindow() {
  const [i, setI] = useState(0);
  const [s, setS] = useState(0);
  useEffect(() => {
    const t = setTimeout(() => {
      if (s + 1 < STAGES.length) setS(s + 1);
      else { setS(0); setI((x) => (x + 1) % EXAMPLES.length); }
    }, STAGES[s][1]);
    return () => clearTimeout(t);
  }, [s]);
  const ex = EXAMPLES[i];
  const stage = STAGES[s][0];
  return (
    <section id="product" className="page-measure scroll-mt-24">
      <motion.div
        initial={{ opacity: 0, y: 24 }}
        whileInView={{ opacity: 1, y: 0 }}
        viewport={{ once: true, margin: "-80px" }}
        transition={{ duration: 0.7, ease: [0.22, 1, 0.36, 1] }}
        className="rounded-2xl bg-hp-plate p-6 shadow-hp-plate max-tablet:p-3"
      >
        <MacWindow title="your agent · editing" className="mx-auto max-w-[980px]">
          <div className="grid grid-cols-[1.35fr_1fr] max-tablet:grid-cols-1">
            <Editor ex={ex} stage={stage} />
            <Verdict ex={ex} stage={stage} />
          </div>
        </MacWindow>
      </motion.div>
      <div className="mt-10 text-center text-sm text-hp-muted">Works inside the agent you already use</div>
      <div className="mx-auto mt-5 flex max-w-[860px] flex-wrap items-center justify-center gap-x-12 gap-y-4 text-[19px] font-semibold tracking-tight text-hp-body/80">
        {["Claude Code", "Codex", "Cursor", "Any MCP client"].map((n) => (
          <span key={n}>{n}</span>
        ))}
      </div>
    </section>
  );
}
