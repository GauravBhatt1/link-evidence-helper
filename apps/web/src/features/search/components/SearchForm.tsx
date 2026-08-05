import { Search } from "lucide-react";
import { type FormEvent, useState } from "react";
import { MAX_SEARCH_QUERY_LENGTH, validateSearchQuery } from "../model/search-query";

export function SearchForm({
  busy,
  externalError,
  onSubmit,
}: {
  busy: boolean;
  externalError: string;
  onSubmit: (query: string) => Promise<boolean>;
}) {
  const [draft, setDraft] = useState("");
  const [localError, setLocalError] = useState("");
  const error = localError || externalError;

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (busy) return;
    const validation = validateSearchQuery(draft);
    if (!validation.ok) {
      setLocalError(validation.error);
      return;
    }
    setLocalError("");
    await onSubmit(validation.query);
  };

  const submitDisabled = busy || !draft.trim();

  return (
    <form className="search-form" aria-label="Development fixture search" onSubmit={submit} noValidate>
      <div className="search-field">
        <label htmlFor="search-query">Movie or TV title</label>
        <p id="search-query-hint">Use one documented fixture alias. Unknown titles return an empty result.</p>
        <div className="search-input-wrap">
          <Search aria-hidden="true" focusable="false" />
          <input
            id="search-query"
            name="query"
            type="search"
            value={draft}
            maxLength={MAX_SEARCH_QUERY_LENGTH}
            aria-describedby={`search-query-hint${error ? " search-query-error" : ""}`}
            aria-invalid={Boolean(error)}
            autoComplete="off"
            placeholder="Example Film"
            className="search-input"
            onChange={(event) => {
              setDraft(event.target.value);
              if (localError) setLocalError("");
            }}
          />
        </div>
        {error && <p id="search-query-error" className="field-error" role="alert">{error}</p>}
      </div>
      <button className="search-submit" type="submit" disabled={submitDisabled} aria-busy={busy}>
        {busy ? "Searching…" : "Search"}
      </button>
    </form>
  );
}
