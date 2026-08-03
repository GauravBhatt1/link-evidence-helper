export function SearchErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <section className="search-state-card search-error-state" role="alert">
      <h2>Development search unavailable</h2>
      <p>{message}</p>
      <button className="error-retry" type="button" onClick={onRetry}>Retry</button>
    </section>
  );
}
