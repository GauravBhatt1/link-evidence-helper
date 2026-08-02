import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { renderRoute } from "../../test/render";

describe("RoutePlaceholder", () => {
  it("states the development boundary without duplicating the page heading", () => {
    renderRoute("/admin");

    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getByText("No application data or backend connection is active here.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /log in|unlock|connect/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  });
});
