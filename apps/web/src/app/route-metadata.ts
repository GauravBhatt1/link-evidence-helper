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
  { id: "search", path: "/", label: "Search", mobileLabel: "Search", documentTitle: "Search · FREEMIUM INDEX", heading: "Search", description: "Development fixture search with no live source connection.", page: "search", icon: Search, mobilePrimary: true },
  { id: "movies", path: "/library/movies", label: "Movies", mobileLabel: "Movies", documentTitle: "Movies · FREEMIUM INDEX", heading: "Movies", description: "Movie library data remains in the Python application during this milestone.", page: "movies", icon: Film, mobilePrimary: true },
  { id: "tv", path: "/library/tv", label: "TV Shows", mobileLabel: "TV", documentTitle: "TV Shows · FREEMIUM INDEX", heading: "TV Shows", description: "TV library data remains in the Python application during this milestone.", page: "tv", icon: Tv, mobilePrimary: true },
  { id: "missing", path: "/library/missing", label: "Missing", mobileLabel: "Missing", documentTitle: "Missing · FREEMIUM INDEX", heading: "Missing", description: "Missing-library data is not connected to this development shell.", page: "missing", icon: CircleAlert, mobilePrimary: true },
  { id: "recent", path: "/library/recent", label: "Recently Added", mobileLabel: "Recent", documentTitle: "Recently Added · FREEMIUM INDEX", heading: "Recently Added", description: "Recently added library data is not connected to this development shell.", page: "recent", icon: Clock3, mobilePrimary: false },
  { id: "admin", path: "/admin", label: "Admin", mobileLabel: "Admin", documentTitle: "Admin · FREEMIUM INDEX", heading: "Admin", description: "Admin authentication and privileged controls are not implemented in this milestone.", page: "admin", icon: ShieldCheck, mobilePrimary: false },
] as const;

export const notFoundMetadata: RouteMetadata = {
  id: "not-found",
  path: "*",
  label: "Page Not Found",
  mobileLabel: "Not Found",
  documentTitle: "Page Not Found · FREEMIUM INDEX",
  heading: "Page Not Found",
  description: "This route is not part of the React shell.",
  page: "not-found",
  icon: FileQuestion,
  mobilePrimary: false,
};

export function metadataForPath(pathname: string): RouteMetadata {
  return routeMetadata.find((route) => route.path === pathname) ?? notFoundMetadata;
}
