import { RadioTower } from "lucide-react";
import type { SearchTransportMode } from "../api/search-transport-config";

export function SearchDevelopmentNotice({ mode = "fixture" }: { mode?: SearchTransportMode }) {
  const apiMode = mode === "api";
  return (
    <aside className="fixture-notice" aria-label={apiMode ? "Live search mode" : "Offline search mode"}>
      <RadioTower aria-hidden="true" focusable="false" />
      <div>
        <strong>
          {apiMode
            ? "Live source search with verified link delivery."
            : "Offline preview search."}
        </strong>
        <p>{apiMode ? "Results come from configured sources and Jellyfin availability checks." : "Connect the API transport to use configured sources."}</p>
      </div>
    </aside>
  );
}
