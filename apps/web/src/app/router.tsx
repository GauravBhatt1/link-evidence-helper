import { createBrowserRouter, type RouteObject } from "react-router-dom";
import { AppShell } from "../components/layout/AppShell";
import { AdminPlaceholderPage } from "../pages/AdminPlaceholderPage";
import { MissingPlaceholderPage } from "../pages/MissingPlaceholderPage";
import { MoviesPlaceholderPage } from "../pages/MoviesPlaceholderPage";
import { NotFoundPage } from "../pages/NotFoundPage";
import { RecentPlaceholderPage } from "../pages/RecentPlaceholderPage";
import { SearchPlaceholderPage } from "../pages/SearchPlaceholderPage";
import { TvPlaceholderPage } from "../pages/TvPlaceholderPage";
import { routeMetadata } from "./route-metadata";

const pages = {
  search: <SearchPlaceholderPage />,
  movies: <MoviesPlaceholderPage />,
  tv: <TvPlaceholderPage />,
  missing: <MissingPlaceholderPage />,
  recent: <RecentPlaceholderPage />,
  admin: <AdminPlaceholderPage />,
} as const;

export const routeObjects: RouteObject[] = [
  {
    element: <AppShell />,
    children: [
      ...routeMetadata.map((route) => ({
        path: route.path,
        element: pages[route.page as keyof typeof pages],
      })),
      { path: "*", element: <NotFoundPage /> },
    ],
  },
];

export const router = createBrowserRouter(routeObjects);
