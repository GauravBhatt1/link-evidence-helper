export function FindLinksAction({
  enabled,
  helper,
  onActivate,
}: {
  enabled: boolean;
  helper: string;
  onActivate: () => void;
}) {
  return (
    <div className="find-links-action">
      <p id="find-links-helper" className="find-links-helper">{helper}</p>
      <button className="find-links-button" type="button" disabled={!enabled} aria-describedby="find-links-helper" onClick={onActivate}>
        Find Links
      </button>
    </div>
  );
}
