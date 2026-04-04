"use client";

import Link from "next/link";

import { ActiveSeasonsBoard } from "../components/ActiveSeasonsBoard";
import GeneratedCardsTicker from "../components/GeneratedCardsTicker";
import OriginsWalletTicker from "../components/OriginsWalletTicker";
import SiteLogoLink from "../components/SiteLogoLink";

export default function HomePage() {
  return (
    <>
      <nav className="site-nav" aria-label="Site">
        <SiteLogoLink />
        <Link href="/me">My dashboard</Link>
      </nav>

      <main>
        <h1>PolyStars</h1>
        <p>
          Browse active seasons below. Connect your wallet, mint, and manage test cards on your
          personal dashboard.
        </p>
      </main>

      <ActiveSeasonsBoard
        footer={
          <p className="season-board-note">
            <Link href="/me">Open your dashboard</Link> to connect a wallet, check mint eligibility,
            and generate cards.
          </p>
        }
      />

      <OriginsWalletTicker />

      <GeneratedCardsTicker />
    </>
  );
}
