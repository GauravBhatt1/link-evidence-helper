export function SearchLoadingSkeleton() {
  return (
    <div className="search-loading" aria-hidden="true">
      <div className="skeleton-poster" />
      <div className="skeleton-lines"><span /><span /><span /></div>
    </div>
  );
}
