"use client";

import { useEffect } from "react";

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <main className="card-detail-page" style={{ padding: "2rem", maxWidth: 520, margin: "2rem auto" }}>
      <h1 style={{ fontSize: "1.25rem", marginBottom: "0.75rem" }}>Something went wrong</h1>
      <p className="season-board-muted" style={{ marginBottom: "1.25rem" }}>
        {error.message || "Try again or reload the page."}
      </p>
      <button type="button" onClick={() => reset()}>
        Try again
      </button>
    </main>
  );
}
