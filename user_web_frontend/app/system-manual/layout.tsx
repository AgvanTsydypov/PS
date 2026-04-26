import { type ReactNode } from "react";

import SystemManualSidebar from "../../components/SystemManualSidebar";

export default function SystemManualLayout({ children }: { children: ReactNode }) {
  return (
    <div className="sm-layout">
      <SystemManualSidebar />
      <main className="sm-content">
        <div className="sm-content-inner">{children}</div>
      </main>
    </div>
  );
}
