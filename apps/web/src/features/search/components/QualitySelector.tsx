import { useId } from "react";

export function QualitySelector({
  qualities,
  selectedQuality,
  onSelect,
}: {
  qualities: string[];
  selectedQuality: string;
  onSelect: (quality: string) => void;
}) {
  const groupName = useId();
  if (!qualities.length) return null;
  return (
    <fieldset className="quality-fieldset">
      <legend>{qualities.length === 1 ? "Selected quality" : "Select one quality"}</legend>
      <div className="quality-list">
        {qualities.map((quality) => (
          <label className={`quality-option${selectedQuality === quality ? " selected" : ""}`} key={quality}>
            <input
              type="radio"
              name={groupName}
              value={quality}
              checked={selectedQuality === quality}
              onChange={() => onSelect(quality)}
            />
            <span>{quality}</span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}
