"use client";
import { useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { Plus } from "@phosphor-icons/react";

const QA = [
  ["What is Pinned?", "A check your coding agent runs before and after it writes code. It knows every public API of each library across releases, so it can tell whether a call exists in the exact version you run, and what to use instead when it doesn't."],
  ["Which agents does it work with?", "Any agent that speaks MCP: Claude Code, Codex, Cursor and others. It also has a plain HTTP API and a command-line tool."],
  ["Which libraries are covered?", "26 popular Python libraries today — pandas, numpy, scipy, scikit-learn, torch, django, flask and more — with every minor release across the ranges that matter. When an agent hits a fact Pinned doesn't have yet, it goes and reads that part of the library."],
  ["How does it get better over time?", "Every time your agent reports whether its code passed or failed, Pinned updates what it knows: answers that worked get stronger, ones that failed fade, and missing facts get filled in from the library itself."],
  ["Does it send my code anywhere?", "The hosted version checks the code you send and keeps the outcome — which calls worked — plus a short error line, not your files. You can run all of Pinned on your own machine instead — it needs no account and no API key."],
  ["Is it free?", "Yes. Pinned is open source, and there is nothing to sign up for."],
];

export function Faq() {
  const [open, setOpen] = useState<number | null>(0);
  return (
    <section id="faq" className="page-measure section-y grid scroll-mt-16 grid-cols-[1fr_1.3fr] gap-16 max-tablet:grid-cols-1 max-tablet:gap-8">
      <div>
        <h2 className="font-display m-0 text-[52px] leading-[1.06] text-hp-ink max-tablet:text-[36px]">
          Frequently asked
          <br />
          questions.
        </h2>
        <p className="mt-5 max-w-xs text-lg text-hp-muted">The short ones are here. For anything else, open an issue.</p>
        <a href="https://github.com/rohit-jsfreaky/pinned/issues" className="mt-6 inline-flex h-9 items-center rounded-lg bg-hp-ink px-4 text-sm font-medium text-hp-on-ink hover:bg-white">Ask on GitHub</a>
      </div>
      <div className="flex flex-col gap-2">
        {QA.map(([q, a], n) => (
          <div key={q} className="rounded-xl bg-hp-field/70 transition-colors hover:bg-hp-field">
            <button onClick={() => setOpen(open === n ? null : n)} className="flex w-full items-center justify-between gap-4 px-5 py-4 text-left">
              <span className="text-[15px] font-semibold text-hp-ink">{q}</span>
              <motion.span animate={{ rotate: open === n ? 45 : 0 }} className="text-hp-muted"><Plus weight="bold" /></motion.span>
            </button>
            <AnimatePresence initial={false}>
              {open === n && (
                <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} exit={{ height: 0, opacity: 0 }} className="overflow-hidden">
                  <p className="m-0 px-5 pb-5 text-[15px] leading-relaxed text-hp-body">{a}</p>
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        ))}
      </div>
    </section>
  );
}
