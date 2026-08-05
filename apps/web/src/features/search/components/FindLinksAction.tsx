import { useId } from "react";

export function FindLinksAction({
  enabled,
  busy = false,
  helper,
  onActivate,
}: {
  enabled: boolean;
  busy?: boolean;
  helper: string;
  onActivate: () => void;
}) {
  const helperId = `find-links-helper-${useId().replace(/:/g, "")}`;
  return (
    <div className="find-links-action">
      <p id={helperId} className="find-links-helper">{helper}</p>
      <button
        className="find-links-button"
        type="button"
        disabled={!enabled || busy}
        aria-describedby={helperId}
        aria-busy={busy}
        onClick={onActivate}
      >
        {busy ? "Checking Links…" : "Find Links"}
      </button>
    </div>
  );
}
