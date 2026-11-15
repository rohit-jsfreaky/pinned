import Link from "next/link";
import { GithubLogo } from "@phosphor-icons/react/dist/ssr";
import { PinMark } from "./ui";

const COLS: [string, [string, string][]][] = [
  ["Product", [["Break it", "/try"], ["Add to your agent", "/#agents"], ["Proof", "/#proof"], ["FAQ", "/#faq"]]],
  ["Developers", [["GitHub", "https://github.com/rohit-jsfreaky/pinned"], ["MCP endpoint", "/mcp"], ["HTTP API", "/v1/libraries"]]],
];

export function Footer() {
  return (
    <footer className="border-t border-hp-line">
      <div className="page-measure flex justify-between gap-10 py-14 max-tablet:flex-col">
        <div className="flex flex-col gap-4">
          <Link href="/" className="flex items-center gap-2 text-[17px] font-semibold tracking-tight text-hp-ink"><PinMark /> Pinned</Link>
          <p className="m-0 max-w-xs text-sm text-hp-muted">Code that runs on the version you actually have.</p>
          <a href="https://github.com/rohit-jsfreaky/pinned" className="text-hp-muted hover:text-hp-ink" aria-label="GitHub"><GithubLogo size={18} weight="bold" /></a>
        </div>
        <div className="grid grid-cols-2 gap-16 max-tablet:gap-10">
          {COLS.map(([h, links]) => (
            <div key={h}>
              <div className="text-sm font-medium text-hp-ink">{h}</div>
              <ul className="mt-3 flex list-none flex-col gap-2.5 p-0">
                {links.map(([l, href]) => (
                  <li key={l}><a href={href} className="text-sm text-hp-muted transition-colors hover:text-hp-ink">{l}</a></li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </div>
      <div className="page-measure flex items-center justify-between border-t border-hp-line py-6 text-[13px] text-hp-muted max-tablet:flex-col max-tablet:gap-2">
        <span>Built on HydraDB</span>
        <span>© 2026 Pinned</span>
      </div>
    </footer>
  );
}
