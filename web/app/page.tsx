import { Nav } from "@/components/landing/Nav";
import { Hero } from "@/components/landing/Hero";
import { ProductWindow } from "@/components/landing/ProductWindow";
import { Problem } from "@/components/landing/Problem";
import { Chain } from "@/components/landing/Chain";
import { Bento } from "@/components/landing/Bento";
import { Agents } from "@/components/landing/Agents";
import { Proof } from "@/components/landing/Proof";
import { Faq } from "@/components/landing/Faq";
import { Cta } from "@/components/landing/Cta";
import { Footer } from "@/components/landing/Footer";

export default function Home() {
  return (
    <>
      <Nav />
      <main>
        <Hero />
        <ProductWindow />
        <Problem />
        <Chain />
        <Bento />
        <Agents />
        <Proof />
        <Faq />
        <Cta />
      </main>
      <Footer />
    </>
  );
}
