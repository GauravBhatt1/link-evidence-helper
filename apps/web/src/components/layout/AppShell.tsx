import { Outlet, useLocation } from "react-router-dom";
import { metadataForPath } from "../../app/route-metadata";
import { RouteFocus } from "../../app/route-focus";
import { DesktopSidebar } from "./DesktopSidebar";
import { MobileBottomNav } from "./MobileBottomNav";
import { MobileHeader } from "./MobileHeader";
import { PageContainer } from "./PageContainer";
import { PageHeader } from "./PageHeader";
import { SkipLink } from "./SkipLink";

export function AppShell() {
  const location = useLocation();
  const metadata = metadataForPath(location.pathname);

  return (
    <div className="app-shell">
      <RouteFocus />
      <SkipLink />
      <DesktopSidebar />
      <MobileHeader />
      <main id="main-content" className="main-content" tabIndex={-1}>
        <PageContainer>
          <PageHeader metadata={metadata} />
          <Outlet />
        </PageContainer>
      </main>
      <MobileBottomNav />
    </div>
  );
}
