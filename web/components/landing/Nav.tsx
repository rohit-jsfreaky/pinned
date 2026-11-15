"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { GithubLogo } from "@phosphor-icons/react";
import { PinMark } from "./ui";

const LINKS = [
  ["Product", "#product"],
  ["Proof", "#proof"],
  ["Agents", "#agents"],
  ["FAQ", "#faq"],
  ["Break it", "/try"],
];

export function Nav() {
  const [scrolled, setScrolled] = useState(false);
  useEffect(() => {
    const on = () => setScrolled(window.scrollY > 8);
    on();
    window.addEventListener("scroll", on, { passive: true });
    return () => window.removeEventListener("scroll", on);
  }, []);
  return (
    <header className={`fixed inset-x-0 top-0 z-50 transition-[background-color,box-shadow] duration-200 ${scrolled ? "bg-hp-page/85 shadow-[0_1px_0_var(--color-hp-line)] backdrop-blur-md" : ""}`}>
      <nav className="page-measure flex h-16 items-center justify-between">
        <Link href="/" className="flex items-center gap-2 text-[17px] font-semibold tracking-tight text-hp-ink">
          <PinMark /> Pinned
        </Link>
        <div className="flex items-center gap-1 max-tablet:hidden">
          {LINKS.map(([label, href]) => (
            <a key={href} href={href} className="rounded-md px-3 py-1.5 text-sm font-medium text-hp-body transition-colors hover:bg-hp-field hover:text-hp-ink">
              {label}
            </a>
          ))}
        </div>
        <div className="flex items-center gap-2">
          <a href="https://github.com/rohit-jsfreaky/pinned" className="rounded-md p-2 text-hp-body hover:bg-hp-field hover:text-hp-ink" aria-label="GitHub">
            <GithubLogo size={18} weight="bold" />
          </a>
          <a href="#agents" className="inline-flex h-9 items-center rounded-lg bg-hp-ink px-4 text-sm font-medium text-hp-on-ink shadow-[inset_0_1px_0_#ffffff80] hover:bg-white">
            Add to your agent
          </a>
        </div>
      </nav>
    </header>
  );
}
