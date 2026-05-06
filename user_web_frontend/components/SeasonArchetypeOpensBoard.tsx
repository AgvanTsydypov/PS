"use client";

// Community achievements board is currently frozen. The endpoints that
// powered this widget (`/api/seasons/catalog`,
// `/api/seasons/{id}/opened-archetypes`) return 503 — see user_web_backend.
// Restore from git history when bringing the feature back online.
export function SeasonArchetypeOpensBoard() {
  return (
    <section className="season-board season-board-standalone season-archetype-opens-board">
      <div className="season-board-title">Community achievements</div>
      <div className="season-archetype-opens-under-construction">UNDER CONSTRUCTION</div>
    </section>
  );
}
