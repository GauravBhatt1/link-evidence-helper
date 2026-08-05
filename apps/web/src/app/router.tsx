import { createBrowserRouter, type RouteObject } from "react-router-dom";
import { AppShell } from "../components/layout/AppShell";
import { AdminPlaceholderPage } from "../pages/AdminPlaceholderPage";
import { MissingPage, MoviesPage, RecentPage, TvPage } from "../pages/LibraryRoutePages";
import { NotFoundPage } from "../pages/NotFoundPage";
import { SearchPage } from "../pages/SearchPage";
import { routeMetadata } from "./route-metadata";

const pages = {
  search: <SearchPage />,
  movies: <MoviesPage />,
  tv: <TvPage />,
  missing: <MissingPage />,
  recent: <RecentPage />,
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
