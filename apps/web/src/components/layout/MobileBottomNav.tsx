import { routeMetadata } from "../../app/route-metadata";
import { NavItem } from "../ui/NavItem";
import { MobileMoreMenu } from "./MobileMoreMenu";

export function MobileBottomNav() {
  return (
    <nav className="mobile-bottom-nav" aria-label="Mobile navigation" data-testid="mobile-bottom-nav">
      {routeMetadata.filter((route) => route.mobilePrimary).map((route) => (
        <NavItem key={route.id} route={route} mobile />
      ))}
      <MobileMoreMenu />
    </nav>
  );
}
