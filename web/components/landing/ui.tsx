import type { ReactNode } from "react";

export function MacWindow({ title, children, className = "" }: { title: string; children: ReactNode; className?: string }) {
  return (
    <div className={`overflow-hidden rounded-xl bg-hp-raised shadow-hp-window ${className}`}>
      <div className="flex h-9 items-center gap-2 border-b border-hp-line bg-hp-field/60 px-3">
        <i className="size-2.5 rounded-full bg-hp-mac-close" />
        <i className="size-2.5 rounded-full bg-hp-mac-minimize" />
        <i className="size-2.5 rounded-full bg-hp-mac-zoom" />
        <span className="mx-auto -translate-x-6 rounded-md bg-hp-raised px-3 py-0.5 font-mono text-[11px] text-hp-muted shadow-hp-lift">{title}</span>
      </div>
      {children}
    </div>
  );
}

export function Badge({ tone, children }: { tone: "ok" | "bad" | "warn" | "neutral"; children: ReactNode }) {
  const t = {
    ok: "bg-ok-soft text-ok",
    bad: "bg-bad-soft text-bad",
    warn: "bg-warn-soft text-warn",
    neutral: "bg-hp-chip text-hp-body",
  }[tone];
  return <span className={`inline-flex items-center gap-1 whitespace-nowrap rounded-md px-2 py-1 text-xs font-medium ${t}`}>{children}</span>;
}

export function Eyebrow({ children }: { children: ReactNode }) {
  return (
    <div className="flex items-center gap-2">
      <span className="size-1.5 rounded-full bg-hp-accent" />
      <span className="font-mono text-xs font-medium uppercase tracking-[0.08em] text-hp-muted">{children}</span>
    </div>
  );
}

export function PinMark({ className = "size-6" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden>
      <rect width="24" height="24" rx="6" fill="#fafafa" />
      <path d="M12 5.5a4 4 0 0 1 4 4c0 2.6-2.4 5.3-3.4 6.3a.85.85 0 0 1-1.2 0C10.4 14.8 8 12.1 8 9.5a4 4 0 0 1 4-4Z" fill="#0e0e0e" />
      <circle cx="12" cy="9.5" r="1.4" fill="#fafafa" />
      <path d="M8.5 18.5h7" stroke="#0e0e0e" strokeWidth="1.4" strokeLinecap="round" />
    </svg>
  );
}

export function ButtonDark({ href, children }: { href: string; children: ReactNode }) {
  return (
    <a href={href} className="inline-flex h-11 items-center justify-center gap-2 rounded-lg bg-hp-ink px-5 text-[15px] font-medium text-hp-on-ink shadow-[inset_0_1px_0_#ffffff80,0_1px_2px_#0006] transition-colors hover:bg-white">
      {children}
    </a>
  );
}

export function ButtonLight({ href, children }: { href: string; children: ReactNode }) {
  return (
    <a href={href} className="inline-flex h-11 items-center justify-center gap-2 rounded-lg bg-hp-raised px-5 text-[15px] font-medium text-hp-ink shadow-hp-raised transition-colors hover:bg-hp-field">
      {children}
    </a>
  );
}
