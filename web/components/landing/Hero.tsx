"use client";
import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { CheckCircle } from "@phosphor-icons/react";
import { EXAMPLES } from "@/lib/examples";
import { ButtonDark, ButtonLight } from "./ui";

// Inline "chips" inside the headline, the way cal.com puts live UI inside its h1.
const chip =
  "relative inline-flex items-center overflow-hidden rounded-[0.2em] bg-hp-field align-[-0.12em] shadow-[inset_0_0_0_1px_var(--color-hp-line)] text-left tracking-normal";

export function Hero() {
  const [i, setI] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setI((x) => (x + 1) % EXAMPLES.length), 2600);
    return () => clearInterval(t);
  }, []);
  const ex = EXAMPLES[i];
  return (
    <section className="page-measure pt-[168px] pb-20 text-center max-desktop:pt-[136px] max-tablet:pt-28">
      <div className="mb-7 flex justify-center max-tablet:mb-5">
        <a href="#agents" className="inline-flex min-h-8 items-center gap-2 rounded-md border border-hp-line bg-hp-page py-0.5 ps-[3px] pe-3 text-sm font-medium text-hp-ink transition-colors hover:bg-hp-field">
          <span className="inline-flex h-6 items-center rounded-[4px] bg-hp-chip px-2 text-[11px] font-medium uppercase leading-none tracking-[0.06em]">New</span>
          Works inside Claude Code, Codex and Cursor
        </a>
      </div>
      <h1 className="font-display mx-auto my-0 max-w-[1020px] text-[76px] leading-[1.08] text-balance text-hp-ink max-desktop:text-[58px] max-tablet:text-[40px]">
        Code that runs on{" "}
        <span className={`${chip} h-[0.86em] px-[0.18em] font-mono text-[0.42em] font-medium`}>
          <AnimatePresence mode="popLayout" initial={false}>
            <motion.span
              key={ex.lib}
              initial={{ y: "100%", opacity: 0 }}
              animate={{ y: 0, opacity: 1 }}
              exit={{ y: "-100%", opacity: 0 }}
              transition={{ type: "spring", bounce: 0.15, duration: 0.5 }}
              className="whitespace-nowrap"
            >
              {ex.lib} {ex.version}
            </motion.span>
          </AnimatePresence>
        </span>{" "}
        the version{" "}
        <span className={`${chip} h-[0.86em] gap-[0.12em] px-[0.16em] text-[0.36em] font-semibold text-ok`}>
          <CheckCircle weight="fill" className="size-[1.05em]" /> valid
        </span>{" "}
        you actually have
      </h1>
      <p className="mx-auto mt-6 mb-0 max-w-[720px] text-xl leading-[1.45] font-medium text-balance text-hp-muted max-tablet:text-[17px]">
        Your coding agent learned from whatever docs were newest. Pinned checks its code against the exact libraries you run,
        hands it the right call when an API changed, and gets sharper every time the code runs.
      </p>
      <div className="mx-auto mt-9 flex max-w-[460px] flex-col justify-center gap-3 sm:flex-row">
        <ButtonDark href="#agents">Add to your agent</ButtonDark>
        <ButtonLight href="/try">Try to break it</ButtonLight>
      </div>
      <p className="mt-4 text-[13px] text-hp-muted">Free and open source. No account needed.</p>
    </section>
  );
}
