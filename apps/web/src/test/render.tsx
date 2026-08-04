import { render } from "@testing-library/react";
import { QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, useRoutes } from "react-router-dom";
import { createAppQueryClient } from "../app/query-client";
import { routeObjects } from "../app/router";

function TestRoutes() {
  return useRoutes(routeObjects);
}

export function renderRoute(path = "/") {
  const queryClient = createAppQueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <TestRoutes />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
