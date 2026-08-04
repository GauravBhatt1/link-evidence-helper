import { FlaskConical } from "lucide-react";
import { FIXTURE_ALIAS_DOCUMENTATION } from "../api/search-fixture-catalog";
import type { SearchTransportMode } from "../api/search-transport-config";

export function SearchDevelopmentNotice({ mode = "fixture" }: { mode?: SearchTransportMode }) {
  const apiMode = mode === "api";
  return (
    <aside className="fixture-notice" aria-label={apiMode ? "Development Go API mode" : "Development fixture mode"}>
      <FlaskConical aria-hidden="true" focusable="false" />
      <div>
        <strong>
          {apiMode
            ? "Development Go API search — sanitized fixtures only; no live sources are contacted."
            : "Development fixture search — no live sources are contacted."}
        </strong>
        <details>
          <summary>Available deterministic fixture aliases</summary>
          <ul>
            {FIXTURE_ALIAS_DOCUMENTATION.map(({ alias, scenario }) => (
              <li key={alias}><code>{alias}</code> — {scenario}</li>
            ))}
          </ul>
        </details>
      </div>
    </aside>
  );
}
