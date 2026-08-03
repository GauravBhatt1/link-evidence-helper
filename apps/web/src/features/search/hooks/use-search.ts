import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useMemo, useRef, useState } from "react";
import type { SearchResponse } from "../../../types/contracts";
import type { SearchTransport } from "../api/search-transport";
import { safeSearchError } from "../api/search-transport-error";
import { validateSearchQuery } from "../model/search-query";

export type SearchPhase = "idle" | "submitting" | "success" | "partial" | "empty" | "error";

type Submission = {
  query: string;
  sequence: number;
};

export function useSearch(transport: SearchTransport) {
  const queryClient = useQueryClient();
  const sequence = useRef(0);
  const [submission, setSubmission] = useState<Submission | null>(null);
  const [starting, setStarting] = useState(false);
  const [formError, setFormError] = useState("");

  const query = useQuery({
    queryKey: ["fixture-search", submission?.query ?? "", submission?.sequence ?? 0],
    enabled: submission !== null,
    queryFn: ({ signal }) => {
      if (!submission) throw new Error("Search submission is missing");
      return transport.search({ query: submission.query }, signal);
    },
  });

  const submit = useCallback(async (rawQuery: string) => {
    const validation = validateSearchQuery(rawQuery);
    if (!validation.ok) {
      setFormError(validation.error);
      return false;
    }
    setFormError("");
    setStarting(true);
    await queryClient.cancelQueries({ queryKey: ["fixture-search"] });
    sequence.current += 1;
    setSubmission({ query: validation.query, sequence: sequence.current });
    setStarting(false);
    return true;
  }, [queryClient]);

  const phase: SearchPhase = useMemo(() => {
    if (!submission) return "idle";
    if (starting || query.isPending || query.isFetching) return "submitting";
    if (query.isError) return "error";
    if (!query.data?.contents.length) return "empty";
    if (query.data.partialFailures.length) return "partial";
    return "success";
  }, [query.data, query.isError, query.isFetching, query.isPending, starting, submission]);

  return {
    phase,
    response: query.data as SearchResponse | undefined,
    submittedQuery: submission?.query ?? "",
    formError,
    safeError: query.isError ? safeSearchError(query.error) : "",
    submit,
    retry: () => query.refetch(),
  };
}
