import { routeMetadata } from "../../app/route-metadata";
import { NavItem } from "../ui/NavItem";

export function DesktopSidebar() {
  return (
    <aside className="desktop-sidebar" data-testid="desktop-sidebar">
      <div className="desktop-brand" aria-label="FREEMIUM INDEX">
        <span className="brand-mark" aria-hidden="true">F</span>
        <span><strong>FREEMIUM</strong><small>INDEX</small></span>
      </div>
      <nav aria-label="Primary navigation" className="desktop-navigation">
        {routeMetadata.map((route) => <NavItem key={route.id} route={route} />)}
      </nav>
      <p className="development-note">React shell · Development only</p>
    </aside>
  );
}
