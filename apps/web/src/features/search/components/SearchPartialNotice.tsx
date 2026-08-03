export function SearchPartialNotice({ failureCount }: { failureCount: number }) {
  return (
    <p className="partial-notice" role="status">
      Some sources did not respond. Results may be incomplete. ({failureCount} {failureCount === 1 ? "failure" : "failures"})
    </p>
  );
}
