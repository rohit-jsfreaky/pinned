"use client";
import { useState, useSyncExternalStore } from "react";
import { motion } from "motion/react";
import { Copy, Check, PushPin, Robot, User } from "@phosphor-icons/react";

type Tab = "Claude Code" | "Codex" | "Cursor";
const snippet = (tab: Tab, url: string) =>
  ({
    "Claude Code": `claude mcp add --transport http pinned ${url}`,
    Codex: `codex mcp add pinned --url ${url}`,
    Cursor: `{ "mcpServers": { "pinned": { "url": "${url}" } } }`,
  })[tab];

const CHAT = [
  { who: "you", text: "Add a running total column to the sales report." },
  { who: "agent", text: "Your project pins pandas 2.0.3. Asking Pinned before I write it." },
  { who: "pinned", text: "how_to · pandas 2.0.3 → use DataFrame.cumsum; DataFrame.append is removed, use pandas.concat" },
  { who: "agent", text: "Done. Tests pass on pandas 2.0.3. Told Pinned it worked." },
] as const;

export function Agents() {
  const [tab, setTab] = useState<Tab>("Claude Code");
  // the MCP endpoint lives on this site's own origin (the reverse proxy routes /mcp to the API)
  const url = useSyncExternalStore(() => () => {}, () => `${window.location.origin}/mcp`, () => "/mcp");
  const [copied, setCopied] = useState(false);
  const code = snippet(tab, url);
  return (
    <section id="agents" className="page-measure section-y grid scroll-mt-16 grid-cols-2 items-center gap-16 max-tablet:grid-cols-1">
      <div>
        <h2 className="font-display m-0 text-[52px] leading-[1.06] text-hp-ink max-tablet:text-[36px]">
          Give your agent
          <br />a memory for versions.
        </h2>
        <p className="mt-5 max-w-md text-lg text-hp-muted">
          One line adds Pinned to Claude Code, Codex or Cursor. Your agent asks before it writes, checks before it runs, and
          tells Pinned what happened after — so the next answer is better.
        </p>
        <div className="mt-8 max-w-[520px] rounded-xl bg-hp-raised shadow-hp-float">
          <div className="flex items-center gap-1 border-b border-hp-line px-2 pt-2">
            {(["Claude Code", "Codex", "Cursor"] as Tab[]).map((t) => (
              <button
                key={t}
                onClick={() => setTab(t)}
                className={`rounded-t-md px-3 pb-2 pt-1 text-[13px] font-medium transition-colors ${t === tab ? "border-b-2 border-hp-ink text-hp-ink" : "text-hp-muted hover:text-hp-ink"}`}
              >
                {t}
              </button>
            ))}
          </div>
          <div className="flex items-start gap-3 p-4">
            <code className="min-w-0 flex-1 break-all font-mono text-[12.5px] leading-6 text-hp-ink">{code}</code>
            <button
              onClick={() => { navigator.clipboard.writeText(code); setCopied(true); setTimeout(() => setCopied(false), 1500); }}
              className="rounded-md p-1.5 text-hp-muted hover:bg-hp-field hover:text-hp-ink"
              aria-label="Copy"
            >
              {copied ? <Check weight="bold" /> : <Copy weight="bold" />}
            </button>
          </div>
        </div>
        <div className="mt-6 flex gap-3">
          <a href="https://github.com/rohit-jsfreaky/pinned" className="inline-flex h-10 items-center rounded-lg bg-hp-ink px-4 text-sm font-medium text-hp-on-ink hover:bg-white">Self-host it</a>
          <a href="/try" className="inline-flex h-10 items-center rounded-lg bg-hp-raised px-4 text-sm font-medium text-hp-ink shadow-hp-raised hover:bg-hp-field">Try in the browser</a>
        </div>
      </div>
      <div className="rounded-2xl bg-hp-plate p-5 shadow-hp-plate">
        <div className="flex flex-col gap-3 rounded-xl bg-hp-raised p-5 shadow-hp-window">
          {CHAT.map((m, n) => (
            <motion.div
              key={n}
              initial={{ opacity: 0, y: 10 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, margin: "-100px" }}
              transition={{ delay: 0.3 + n * 0.55, duration: 0.4 }}
              className={`flex gap-3 ${m.who === "you" ? "justify-end" : ""}`}
            >
              {m.who !== "you" && (
                <span className={`grid size-7 shrink-0 place-items-center rounded-full ${m.who === "pinned" ? "bg-hp-ink text-hp-on-ink" : "bg-hp-field text-hp-body"}`}>
                  {m.who === "pinned" ? <PushPin weight="fill" size={13} /> : <Robot weight="fill" size={13} />}
                </span>
              )}
              <div
                className={`max-w-[85%] rounded-xl px-3.5 py-2.5 text-[13.5px] ${
                  m.who === "you" ? "bg-hp-ink text-hp-on-ink" : m.who === "pinned" ? "bg-hp-field font-mono text-[12px] text-hp-ink shadow-hp-plate" : "bg-hp-field text-hp-ink"
                }`}
              >
                {m.text}
              </div>
              {m.who === "you" && (
                <span className="grid size-7 shrink-0 place-items-center rounded-full bg-hp-field text-hp-body"><User weight="fill" size={13} /></span>
              )}
            </motion.div>
          ))}
        </div>
      </div>
    </section>
  );
}
