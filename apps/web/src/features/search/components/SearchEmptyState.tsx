export function SearchEmptyState({ query }: { query: string }) {
  return (
    <section className="search-state-card search-empty-state" aria-label="No fixture results">
      <h2>No development fixture matches this search.</h2>
      <p>No canonical fixture is registered for “{query}”. Use one of the documented aliases above.</p>
    </section>
  );
}
