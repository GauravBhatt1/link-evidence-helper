import { render } from "@testing-library/react";
import { MemoryRouter, useRoutes } from "react-router-dom";
import { routeObjects } from "../app/router";

function TestRoutes() {
  return useRoutes(routeObjects);
}

export function renderRoute(path = "/") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <TestRoutes />
    </MemoryRouter>,
  );
}
