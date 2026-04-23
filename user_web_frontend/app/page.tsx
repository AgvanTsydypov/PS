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
            title="Seasons activity"
            footer={
              <p className="season-board-note">
                <Link href="/me">Open your dashboard</Link> to connect a wallet, check mint
                eligibility, and generate stars.
              </p>
            }
          />
          <SeasonArchetypeOpensBoard />
        </div>
      }
    />
  );
}
