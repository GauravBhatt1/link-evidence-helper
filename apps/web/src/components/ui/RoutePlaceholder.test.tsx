import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { renderRoute } from "../../test/render";

describe("RoutePlaceholder", () => {
  it("states the live maintenance boundary without duplicating the page heading", () => {
    renderRoute("/not-a-real-route");

    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getByText("Use the linked live tool for advanced maintenance.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /log in|unlock|connect/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  });
});
