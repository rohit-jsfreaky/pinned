import { ButtonDark, ButtonLight } from "./ui";

export function Cta() {
  return (
    <section className="page-measure section-y text-center">
      <h2 className="font-display mx-auto m-0 max-w-3xl text-[68px] leading-[1.04] text-hp-ink max-tablet:text-[40px]">
        Ship code that runs
        <br />
        the first time.
      </h2>
      <p className="mx-auto mt-6 max-w-lg text-lg text-hp-muted">Free and open source. One line in your agent, no account, no key.</p>
      <div className="mt-9 flex justify-center gap-3 max-tablet:flex-col">
        <ButtonDark href="#agents">Add to your agent</ButtonDark>
        <ButtonLight href="/try">Try to break it</ButtonLight>
      </div>
    </section>
  );
}
