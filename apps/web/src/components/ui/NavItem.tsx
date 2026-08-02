import { NavLink } from "react-router-dom";
import type { RouteMetadata } from "../../app/route-metadata";

export function NavItem({ route, mobile = false, onNavigate }: { route: RouteMetadata; mobile?: boolean; onNavigate?: () => void }) {
  const Icon = route.icon;
  return (
    <NavLink
      to={route.path}
      end={route.path === "/"}
      onClick={onNavigate}
      className={({ isActive }) => `${mobile ? "mobile-nav-link" : "nav-link"}${isActive ? " active" : ""}`}
    >
      <Icon aria-hidden="true" focusable="false" />
      <span>{mobile ? route.mobileLabel : route.label}</span>
    </NavLink>
  );
}
