import { FlaskConical } from "lucide-react";
import { FIXTURE_ALIAS_DOCUMENTATION } from "../api/search-fixture-catalog";

export function SearchDevelopmentNotice() {
  return (
    <aside className="fixture-notice" aria-label="Development fixture mode">
      <FlaskConical aria-hidden="true" focusable="false" />
      <div>
        <strong>Development fixture search — no live sources are contacted.</strong>
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
