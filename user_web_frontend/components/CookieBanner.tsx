"use client";

import { useEffect, useState } from "react";

const STORAGE_KEY = "polystars_cookie_consent";

export default function CookieBanner() {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    if (!localStorage.getItem(STORAGE_KEY)) setVisible(true);
  }, []);

  function dismiss() {
    localStorage.setItem(STORAGE_KEY, "1");
    setVisible(false);
  }

  if (!visible) return null;

  return (
    <div className="cookie-banner" role="dialog" aria-label="Cookie notice">
      <p className="cookie-banner-text">
        This site uses an HttpOnly session cookie for authentication. No tracking or advertising cookies are used.
      </p>
      <button className="cookie-banner-btn" onClick={dismiss}>
        Got it
      </button>
    </div>
  );
}
