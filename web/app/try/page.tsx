import type { Metadata } from "next";
import { Nav } from "@/components/landing/Nav";
import { Footer } from "@/components/landing/Footer";
import { Playground } from "@/components/try/Playground";

export const metadata: Metadata = {
  title: "Break it — Pinned",
  description: "Paste any Python code, pick a library version, and see what Pinned says about every call.",
};

export default function TryPage() {
  return (
    <>
      <Nav />
      <main className="page-measure pt-32 pb-24 max-tablet:pt-24">
        <div className="grid grid-cols-[1fr_2fr] gap-12 max-desktop:grid-cols-1 max-desktop:gap-8">
          <div>
            <h1 className="font-display m-0 text-[52px] leading-[1.06] text-hp-ink max-tablet:text-[36px]">
              Try to break it.
            </h1>
            <p className="mt-5 text-lg text-hp-muted">
              Paste any Python code, pick the library and the exact version you run, and see what Pinned says about every
              call — with the proof. Or ask how to do something, for that version.
            </p>
            <ul className="mt-8 flex list-none flex-col gap-3 p-0 text-sm text-hp-body">
              <li>• Old calls that were removed</li>
              <li>• Keyword arguments that changed</li>
              <li>• Calls from releases newer than yours</li>
              <li>• Import aliases, members and re-exports</li>
            </ul>
          </div>
          <Playground />
        </div>
      </main>
      <Footer />
    </>
  );
}
