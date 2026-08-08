export function SearchEmptyState({ query }: { query: string }) {
  return (
    <section className="search-state-card search-empty-state" aria-label="No live results">
      <h2>No live source matches this search.</h2>
      <p>No configured source returned a result for “{query}”.</p>
    </section>
  );
}
