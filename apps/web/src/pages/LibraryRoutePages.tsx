import { LibraryPage } from "../features/library/components/LibraryPage";

export function MoviesPage() {
  return <LibraryPage view="movies" />;
}

export function TvPage() {
  return <LibraryPage view="tv" />;
}

export function MissingPage() {
  return <LibraryPage view="missing" />;
}

export function RecentPage() {
  return <LibraryPage view="recent" />;
}
