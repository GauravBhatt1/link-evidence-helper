import { useLocation } from "react-router-dom";
import { metadataForPath } from "../../app/route-metadata";

export function RoutePlaceholder() {
  const metadata = metadataForPath(useLocation().pathname);
  return (
    <section className="route-placeholder" aria-label={`${metadata.heading} development status`}>
      <p className="placeholder-label">Development placeholder</p>
      <p>{metadata.description}</p>
      <p className="placeholder-boundary">No application data or backend connection is active here.</p>
    </section>
  );
}
