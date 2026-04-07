"use client";

import Link from "next/link";

import { ActiveSeasonsBoard } from "../components/ActiveSeasonsBoard";
import GeneratedCardsTicker from "../components/GeneratedCardsTicker";
import SiteLogoLink from "../components/SiteLogoLink";

export default function HomePage() {
  return (
    <>
      <div className="home-hero-logo" aria-label="PolyStars">
        <SiteLogoLink showWordmark />
      </div>

      <div className="home-page-top-gap" aria-hidden="true" />

      <ActiveSeasonsBoard
        footer={
          <p className="season-board-note">
            <Link href="/me">Open your dashboard</Link> to connect a wallet, check mint eligibility,
            and generate cards.
          </p>
        }
      />

      <GeneratedCardsTicker />
    </>
  );
}
