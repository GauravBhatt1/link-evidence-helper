import { useLocation } from "react-router-dom";
import { metadataForPath } from "../../app/route-metadata";

export function RoutePlaceholder() {
  const metadata = metadataForPath(useLocation().pathname);
  return (
    <section className="route-placeholder" aria-label={`${metadata.heading} status`}>
      <p className="placeholder-label">Live tools</p>
      <p>{metadata.description}</p>
      <p className="placeholder-boundary">Use the linked live tool for advanced maintenance.</p>
    </section>
  );
}
