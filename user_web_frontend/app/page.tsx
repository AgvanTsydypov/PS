import Link from "next/link";

import { ActiveSeasonsBoard } from "../components/ActiveSeasonsBoard";
import GeneratedCardsTicker from "../components/GeneratedCardsTicker";
import { SeasonArchetypeOpensBoard } from "../components/SeasonArchetypeOpensBoard";

export default function HomePage() {
  return (
    <GeneratedCardsTicker
      centerContent={
        <div className="home-showcase-center-stack">
          <div className="home-hero-logo" aria-label="PolyStars">
            <img src="/polystars-long.svg" alt="PolyStars" className="home-hero-wordmark" />
          </div>
          <ActiveSeasonsBoard
            title="ACTIVE SEASONS"
            footer={
              <p className="season-board-note">
                <Link href="/me">OPEN DASHBOARD</Link>, CONNECT YOUR WALLET, CHECK ELIGIBILITY, GET YOUR <strong>STARS</strong>.
              </p>
            }
          />
          <SeasonArchetypeOpensBoard />
          <div className="home-sm-action-row">
            <Link href="/system-manual" className="season-mint-button">
              System Manual
            </Link>
            <Link href="/events" className="season-mint-button home-events-button">
              Events
            </Link>
          </div>
        </div>
      }
    />
  );
}
