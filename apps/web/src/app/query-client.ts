import { QueryClient } from "@tanstack/react-query";

export const SEARCH_QUERY_GC_TIME_MS = 5 * 60 * 1000;

export function createAppQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        refetchOnWindowFocus: false,
        refetchOnReconnect: false,
        staleTime: 0,
        gcTime: SEARCH_QUERY_GC_TIME_MS,
      },
      mutations: {
        retry: false,
      },
    },
  });
}

export const appQueryClient = createAppQueryClient();
