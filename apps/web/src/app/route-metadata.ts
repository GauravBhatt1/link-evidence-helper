import type { LucideIcon } from "lucide-react";
import { CircleAlert, Clock3, Film, FileQuestion, Search, ShieldCheck, Tv } from "lucide-react";

export type RoutePage = "search" | "movies" | "tv" | "missing" | "recent" | "admin" | "not-found";

export type RouteMetadata = {
  id: string;
  path: string;
  label: string;
  mobileLabel: string;
  documentTitle: string;
  heading: string;
  description: string;
  page: RoutePage;
  icon: LucideIcon;
  mobilePrimary: boolean;
};

export const routeMetadata: readonly RouteMetadata[] = [
  { id: "search", path: "/", label: "Search", mobileLabel: "Search", documentTitle: "Search · FREEMIUM INDEX", heading: "Search", description: "Search releases and open verified Delivery Links.", page: "search", icon: Search, mobilePrimary: true },
  { id: "movies", path: "/library/movies", label: "Movies", mobileLabel: "Movies", documentTitle: "Movies · FREEMIUM INDEX", heading: "Movies", description: "Browse canonical movie-library items from the selected safe data transport.", page: "movies", icon: Film, mobilePrimary: true },
  { id: "tv", path: "/library/tv", label: "TV Shows", mobileLabel: "TV", documentTitle: "TV Shows · FREEMIUM INDEX", heading: "TV Shows", description: "Browse series, seasons, and episodes without exposing integration internals.", page: "tv", icon: Tv, mobilePrimary: true },
  { id: "missing", path: "/library/missing", label: "Missing", mobileLabel: "Missing", documentTitle: "Missing · FREEMIUM INDEX", heading: "Missing", description: "Review missing and partially available library items.", page: "missing", icon: CircleAlert, mobilePrimary: true },
  { id: "recent", path: "/library/recent", label: "Recently Added", mobileLabel: "Recent", documentTitle: "Recently Added · FREEMIUM INDEX", heading: "Recently Added", description: "See library items ordered by their canonical added date.", page: "recent", icon: Clock3, mobilePrimary: false },
  { id: "admin", path: "/admin", label: "Admin", mobileLabel: "Admin", documentTitle: "Admin · FREEMIUM INDEX", heading: "Admin", description: "Admin authentication and privileged controls are not implemented yet.", page: "admin", icon: ShieldCheck, mobilePrimary: false },
] as const;

export const notFoundMetadata: RouteMetadata = {
  id: "not-found",
  path: "*",
  label: "Page Not Found",
  mobileLabel: "Not Found",
  documentTitle: "Page Not Found · FREEMIUM INDEX",
  heading: "Page Not Found",
  description: "This route is not part of the application.",
  page: "not-found",
  icon: FileQuestion,
  mobilePrimary: false,
};

export function metadataForPath(pathname: string): RouteMetadata {
  return routeMetadata.find((route) => route.path === pathname) ?? notFoundMetadata;
}
