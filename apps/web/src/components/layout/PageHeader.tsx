import type { RouteMetadata } from "../../app/route-metadata";

export function PageHeader({ metadata }: { metadata: RouteMetadata }) {
  const Icon = metadata.icon;
  return (
    <header className="page-header">
      <div className="page-heading-icon" aria-hidden="true"><Icon aria-hidden="true" focusable="false" /></div>
      <div>
        <p className="page-kicker">Live workspace</p>
        <h1 id="route-heading" tabIndex={-1}>{metadata.heading}</h1>
      </div>
    </header>
  );
}
