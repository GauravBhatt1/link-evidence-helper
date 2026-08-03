import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { ReleaseVariantViewModel } from "../model/search-view-model";
import { QualitySelector } from "./QualitySelector";
import { ReleaseVariantCard } from "./ReleaseVariantCard";

const variant: ReleaseVariantViewModel = {
  variantId: "variant-safe",
  language: "Hindi-English",
  releaseType: "WEB-DL",
  qualities: ["720p", "1080p"],
  sourceCount: 2,
  packLabel: "",
};

describe("release and quality controls", () => {
  it("uses a native radio and renders only approved release fields", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    const { container } = render(<ReleaseVariantCard variant={variant} name="release" checked={false} onSelect={onSelect} />);
    const radio = screen.getByRole("radio", { name: /Hindi-English/ });
    await user.click(radio);
    expect(onSelect).toHaveBeenCalledOnce();
    expect(container).toHaveTextContent("WEB-DL");
    expect(container).toHaveTextContent("720p · 1080p");
    expect(container).toHaveTextContent("2 sources");
    expect(container.querySelectorAll("button")).toHaveLength(0);
  });

  it("uses one native quality radio group", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    render(<QualitySelector qualities={["720p", "1080p"]} selectedQuality="720p" onSelect={onSelect} />);
    const group = screen.getByRole("group", { name: "Select one quality" });
    expect(withinGroup(group, "720p")).toBeChecked();
    await user.click(withinGroup(group, "1080p"));
    expect(onSelect).toHaveBeenCalledWith("1080p");
  });
});

function withinGroup(group: HTMLElement, name: string) {
  const input = Array.from(group.querySelectorAll<HTMLInputElement>('input[type="radio"]')).find((item) => item.value === name);
  if (!input) throw new Error(`Missing quality ${name}`);
  return input;
}
