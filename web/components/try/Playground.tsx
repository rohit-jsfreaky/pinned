"use client";
import { useEffect, useMemo, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { ArrowRight, CheckCircle, CircleNotch, Question, Warning, Play } from "@phosphor-icons/react";
import { EXAMPLES } from "@/lib/examples";
import { Badge } from "@/components/landing/ui";

type Lib = { name: string; versions: { version: string }[] };
type Hop = { src: string; rel: string; dst: string; note?: string };
type Finding = { call: string; status: string; fix: string; chain: Hop[]; line: number | null };
type Item = { path: string; status: string; chain: Hop[]; signature: string; note: string };
type ApiError = { error?: { code: string; message: string } };

const TONE: Record<string, "ok" | "bad" | "warn" | "neutral"> = {
  valid: "ok", removed: "bad", missing: "bad", renamed: "bad", changed: "warn", added_later: "warn", deprecated: "warn", unknown: "neutral",
};
const LABEL: Record<string, string> = {
  valid: "Valid", removed: "Removed", missing: "Not in this version", renamed: "Renamed", changed: "Changed",
  added_later: "Added later", deprecated: "Deprecated", unknown: "Unknown",
};

async function call<T>(path: string, body?: unknown): Promise<T> {
  const r = await fetch(path, body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {});
  const data = (await r.json().catch(() => ({}))) as T & ApiError;
  if (!r.ok) throw new Error(data.error?.message ?? `Pinned answered ${r.status}`);
  return data;
}

function Chain({ hops }: { hops: Hop[] }) {
  if (!hops.length) return null;
  return (
    <div className="mt-3 flex flex-wrap items-center gap-1.5 font-mono text-[11.5px] text-hp-body">
      {hops.map((h, n) => (
        <span key={n} className="flex items-center gap-1.5">
          {n === 0 && <span className="rounded bg-hp-field px-1.5 py-0.5">{h.src}</span>}
          <span className="text-hp-muted">—{h.rel}→</span>
          <span className="rounded bg-hp-field px-1.5 py-0.5">{h.dst}</span>
        </span>
      ))}
    </div>
  );
}

export function Playground() {
  const [libs, setLibs] = useState<Lib[] | null>(null);
  const [offline, setOffline] = useState<string | null>(null);
  const [pickedLib, setLib] = useState("pandas");
  const [picked, setVer] = useState("");
  const [mode, setMode] = useState<"check" | "how">("check");
  const [code, setCode] = useState(EXAMPLES[0].before.join("\n"));
  const [task, setTask] = useState("add a row to a DataFrame");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [findings, setFindings] = useState<Finding[] | null>(null);
  const [items, setItems] = useState<Item[] | null>(null);
  const [took, setTook] = useState<number | null>(null);

  useEffect(() => {
    call<{ libraries: Lib[] }>("/v1/libraries")
      .then((d) => setLibs(d.libraries))
      .catch((e) => setOffline(String(e.message ?? e)));
  }, []);
  // the picked library if the graph has it, else the graph's first library
  const lib = !libs || libs.some((l) => l.name === pickedLib) ? pickedLib : libs[0]?.name ?? pickedLib;
  const versions = useMemo(() => libs?.find((l) => l.name === lib)?.versions.map((v) => v.version) ?? [], [libs, lib]);
  // the picked version if this library has it, else its newest one
  const ver = versions.includes(picked) ? picked : versions.at(-1) ?? "";

  const loadExample = (n: number) => {
    const ex = EXAMPLES[n];
    setLib(ex.lib);
    setCode(ex.before.join("\n"));
    const vs = libs?.find((l) => l.name === ex.lib)?.versions.map((v) => v.version) ?? [];
    setVer(vs.includes(ex.version) ? ex.version : vs.at(-1) ?? "");
  };

  const run = async () => {
    setBusy(true); setErr(null); setFindings(null); setItems(null);
    try {
      if (mode === "check") {
        const d = await call<{ findings: Finding[]; took_ms: number }>("/v1/check", { code, library: lib, version: ver });
        setFindings(d.findings); setTook(d.took_ms);
      } else {
        const d = await call<{ items: Item[]; took_ms: number }>("/v1/how", { task, code, library: lib, version: ver, k: 8 });
        setItems(d.items); setTook(d.took_ms);
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="rounded-2xl bg-hp-plate p-4 shadow-hp-plate max-tablet:p-2">
      <div className="overflow-hidden rounded-xl bg-hp-raised shadow-hp-window">
        <div className="flex flex-wrap items-center gap-2 border-b border-hp-line px-4 py-3">
          {(["check", "how"] as const).map((m) => (
            <button key={m} onClick={() => setMode(m)}
              className={`rounded-md px-3 py-1.5 text-[13px] font-medium ${mode === m ? "bg-hp-ink text-hp-on-ink" : "text-hp-body hover:bg-hp-field"}`}>
              {m === "check" ? "Check my code" : "Ask how"}
            </button>
          ))}
          <div className="ml-auto flex items-center gap-2">
            <select value={lib} onChange={(e) => setLib(e.target.value)} disabled={!libs}
              className="h-8 rounded-md border border-hp-line bg-hp-raised px-2 font-mono text-[12.5px] text-hp-ink">
              {(libs ?? [{ name: lib, versions: [] }]).map((l) => <option key={l.name}>{l.name}</option>)}
            </select>
            <select value={ver} onChange={(e) => setVer(e.target.value)} disabled={!versions.length}
              className="h-8 rounded-md border border-hp-line bg-hp-raised px-2 font-mono text-[12.5px] text-hp-ink">
              {versions.map((v) => <option key={v}>{v}</option>)}
            </select>
          </div>
        </div>
        {mode === "how" && (
          <input value={task} onChange={(e) => setTask(e.target.value)} placeholder="What should the code do?"
            className="w-full border-b border-hp-line px-5 py-3 text-[15px] text-hp-ink outline-none placeholder:text-hp-muted" />
        )}
        <textarea value={code} onChange={(e) => setCode(e.target.value)} spellCheck={false}
          placeholder={mode === "how" ? "Optional: your current code" : "Paste Python code"}
          className="block h-[240px] w-full resize-y bg-hp-raised px-5 py-4 font-mono text-[13px] leading-[1.7] text-hp-ink outline-none" />
        <div className="flex flex-wrap items-center gap-2 border-t border-hp-line bg-hp-field/50 px-4 py-3">
          <span className="text-[12px] text-hp-muted">Examples:</span>
          {EXAMPLES.map((ex, n) => (
            <button key={ex.call} onClick={() => loadExample(n)} className="rounded-md bg-hp-raised px-2 py-1 font-mono text-[11.5px] text-hp-body shadow-hp-lift hover:text-hp-ink">
              {ex.bad}
            </button>
          ))}
          <button onClick={run} disabled={busy || !!offline || !ver}
            className="ml-auto inline-flex h-9 items-center gap-2 rounded-lg bg-hp-ink px-4 text-sm font-medium text-hp-on-ink disabled:opacity-50">
            {busy ? <CircleNotch className="animate-spin" weight="bold" /> : <Play weight="fill" />} {mode === "check" ? "Check" : "Ask"}
          </button>
        </div>
      </div>

      <div className="mt-4 flex flex-col gap-2">
        {offline && (
          <div className="rounded-xl bg-hp-raised p-4 text-sm text-hp-body shadow-hp-lift">
            Pinned&apos;s API is not reachable from this page right now ({offline}). Run it yourself with <code className="font-mono">pinned serve</code>.
          </div>
        )}
        {err && <div className="rounded-xl bg-bad-soft p-4 text-sm text-bad">{err}</div>}
        <AnimatePresence>
          {findings?.length === 0 && (
            <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="rounded-xl bg-hp-raised p-4 text-sm text-hp-body shadow-hp-lift">
              No {lib} calls found in this code.
            </motion.div>
          )}
          {findings?.map((f, n) => (
            <motion.div key={`${f.call}-${n}`} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: n * 0.05 }}
              className="rounded-xl bg-hp-raised p-4 shadow-hp-lift">
              <div className="flex items-center justify-between gap-3">
                <span className="truncate font-mono text-[13px] text-hp-ink">{f.call}{f.line ? <span className="text-hp-muted"> · line {f.line}</span> : null}</span>
                <Badge tone={TONE[f.status] ?? "neutral"}>
                  {f.status === "valid" ? <CheckCircle weight="fill" /> : f.status === "unknown" ? <Question weight="fill" /> : <Warning weight="fill" />}
                  {LABEL[f.status] ?? f.status}
                </Badge>
              </div>
              {f.fix && <div className="mt-2 flex items-center gap-2 text-sm text-hp-body"><ArrowRight weight="bold" className="text-ok" /> {f.fix}</div>}
              <Chain hops={f.chain} />
            </motion.div>
          ))}
          {items?.map((it, n) => (
            <motion.div key={`${it.path}-${n}`} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: n * 0.05 }}
              className="rounded-xl bg-hp-raised p-4 shadow-hp-lift">
              <div className="flex items-center justify-between gap-3">
                <span className="truncate font-mono text-[13px] text-hp-ink">{it.path}</span>
                <Badge tone={TONE[it.status] ?? "neutral"}>{LABEL[it.status] ?? it.status}</Badge>
              </div>
              {it.signature && <div className="mt-2 truncate font-mono text-[12px] text-hp-muted">{it.signature}</div>}
              {it.note && <div className="mt-1 text-sm text-hp-body">{it.note}</div>}
              <Chain hops={it.chain} />
            </motion.div>
          ))}
        </AnimatePresence>
        {took != null && (findings || items) && <div className="text-right font-mono text-[11px] text-hp-muted">answered in {Math.round(took)} ms</div>}
      </div>
    </div>
  );
}
