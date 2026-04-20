import "./globals.css";
import type { Metadata } from "next";
import { ReactNode } from "react";

import FoucGuard from "../components/FoucGuard";
import SiteMaintenanceStrip from "../components/SiteMaintenanceStrip";
import SiteSocialFooter from "../components/SiteSocialFooter";

export const metadata: Metadata = {
  title: "PolyStars User",
  description: "Wallet sign-in for PolyStars users",
  icons: {
    icon: [{ url: "/logo.svg", type: "image/svg+xml" }],
  },
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <head>
        {/* Orbitron is used inside SVG card images; preloading it at page level
            ensures the font is always available when SVGs are rendered inline. */}
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        {/* Orbitron for card SVGs + display type; Space Grotesk for UI body;
            JetBrains Mono for addresses / monospaced metadata. Matches byld.dev's
            angular, brutalist design language while keeping strong readability. */}
        <link
          href="https://fonts.googleapis.com/css2?family=Orbitron:wght@700&family=Space+Grotesk:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap"
          rel="stylesheet"
        />
        {/* FOUC guard — hide the body until React has hydrated so that
            stylesheets injected at runtime (Next.js HMR runtime, per-route
            CSS chunks, async CSS-in-JS) have actually been applied. Without
            this, dev-mode briefly shows every section as a plain block at
            the top-left of the viewport (auth-info-panel before its
            position: fixed kicks in, season boards before margin: auto kicks
            in, etc.) and then snap into place. The reveal is performed by
            the FoucGuard client component below in a useEffect; the inline
            <script> is only a safety-net in case hydration never happens or
            JS is disabled, and waits for the load event (after which all
            stylesheet links and CSS chunks have definitely loaded). */}
        <style
          dangerouslySetInnerHTML={{
            __html:
              "body{visibility:hidden}body.app-ready{visibility:visible}",
          }}
        />
        <script
          dangerouslySetInnerHTML={{
            __html:
              "(function(){function r(){if(document.body&&!document.body.classList.contains('app-ready')){document.body.classList.add('app-ready');}}if(document.readyState==='complete'){r();}else{window.addEventListener('load',r,{once:true});}setTimeout(r,4000);})();",
          }}
        />
      </head>
      <body>
        <FoucGuard />
        <SiteMaintenanceStrip />
        {children}
        <SiteSocialFooter />
      </body>
    </html>
  );
}
