import { useCallback, useEffect, useRef, useState } from "react";

import type { Job, ResolutionRequest, ResolutionResult } from "../../../types/contracts";
import {
  createResolutionIdempotencyKey,
  ResolutionClient,
  resolutionClient,
  resolutionStatusMessage,
  TERMINAL_JOB_STATES,
} from "../api/resolution-client";

export type ResolutionPhase =
  | "idle"
  | "submitting"
  | "running"
  | "verified"
  | "partial"
  | "blocked"
  | "failed"
  | "cancelled"
  | "error";

export type ResolutionViewState = {
  phase: ResolutionPhase;
  request: ResolutionRequest | null;
  job: Job | null;
  result: ResolutionResult | null;
  statusMessage: string;
  error: string;
};

type ActiveRequest = {
  generation: number;
  controller: AbortController;
  idempotencyKey: string;
  jobId: string | null;
};

type UseResolutionOptions = {
  client?: ResolutionClient;
  pollIntervalMs?: number;
  maximumDurationMs?: number;
};

const initialState: ResolutionViewState = {
  phase: "idle",
  request: null,
  job: null,
  result: null,
  statusMessage: "",
  error: "",
};

export function useResolution({
  client = resolutionClient,
  pollIntervalMs = 500,
  maximumDurationMs = 120_000,
}: UseResolutionOptions = {}) {
  const [state, setState] = useState<ResolutionViewState>(initialState);
  const activeRef = useRef<ActiveRequest | null>(null);
  const generationRef = useRef(0);
  const mountedRef = useRef(true);

  const unsubscribe = useCallback((active: ActiveRequest) => {
    if (!active.jobId) return;
    void client.unsubscribe(active.jobId, active.idempotencyKey).catch(() => undefined);
  }, [client]);

  const stopActive = useCallback((notifyServer: boolean) => {
    const active = activeRef.current;
    if (!active) return;
    activeRef.current = null;
    active.controller.abort();
    if (notifyServer) unsubscribe(active);
  }, [unsubscribe]);

  const reset = useCallback(() => {
    generationRef.current += 1;
    stopActive(true);
    if (mountedRef.current) setState(initialState);
  }, [stopActive]);

  const cancel = useCallback(() => {
    generationRef.current += 1;
    stopActive(true);
    if (mountedRef.current) {
      setState((current) => ({
        ...current,
        phase: "cancelled",
        statusMessage: "Link request cancelled.",
        error: "",
      }));
    }
  }, [stopActive]);

  const start = useCallback(async (request: ResolutionRequest) => {
    generationRef.current += 1;
    stopActive(true);
    const generation = generationRef.current;
    const active: ActiveRequest = {
      generation,
      controller: new AbortController(),
      idempotencyKey: createResolutionIdempotencyKey(),
      jobId: null,
    };
    activeRef.current = active;
    setState({
      phase: "submitting",
      request,
      job: null,
      result: null,
      statusMessage: "Creating link request…",
      error: "",
    });

    const updateJob = (job: Job, result: ResolutionResult | null = null) => {
      if (!mountedRef.current || activeRef.current?.generation !== generation) return;
      const phase: ResolutionPhase = TERMINAL_JOB_STATES.has(job.state)
        ? terminalPhase(job.state)
        : "running";
      setState({
        phase,
        request,
        job,
        result,
        statusMessage: result?.message || resolutionStatusMessage(job),
        error: phase === "failed" || phase === "blocked" ? result?.message ?? resolutionStatusMessage(job) : "",
      });
    };

    try {
      let job = await client.create(request, active.idempotencyKey, active.controller.signal);
      if (activeRef.current?.generation !== generation) return;
      active.jobId = job.jobId;
      updateJob(job);

      const deadline = Date.now() + maximumDurationMs;
      while (!TERMINAL_JOB_STATES.has(job.state)) {
        if (Date.now() >= deadline) {
          throw new Error("The link request took too long to complete.");
        }
        await abortableDelay(pollIntervalMs, active.controller.signal);
        job = await client.get(job.jobId, active.controller.signal);
        updateJob(job);
      }
      const result = client.parseResult(job);
      updateJob(job, result);
      if (activeRef.current?.generation === generation) activeRef.current = null;
    } catch (error) {
      if (active.controller.signal.aborted || activeRef.current?.generation !== generation) return;
      activeRef.current = null;
      const message = error instanceof Error && error.message
        ? error.message
        : "The link request could not be completed.";
      if (mountedRef.current) {
        setState({
          phase: "error",
          request,
          job: null,
          result: null,
          statusMessage: "",
          error: message,
        });
      }
    }
  }, [client, maximumDurationMs, pollIntervalMs, stopActive]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      generationRef.current += 1;
      stopActive(true);
    };
  }, [stopActive]);

  return { ...state, start, cancel, reset };
}

function terminalPhase(state: Job["state"]): ResolutionPhase {
  switch (state) {
    case "verified":
      return "verified";
    case "partial":
      return "partial";
    case "blocked":
      return "blocked";
    case "failed":
      return "failed";
    case "cancelled":
      return "cancelled";
    default:
      return "running";
  }
}

function abortableDelay(milliseconds: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(resolve, Math.max(0, milliseconds));
    const abort = () => {
      window.clearTimeout(timer);
      reject(new DOMException("Aborted", "AbortError"));
    };
    if (signal.aborted) {
      abort();
      return;
    }
    signal.addEventListener("abort", abort, { once: true });
    window.setTimeout(() => signal.removeEventListener("abort", abort), Math.max(0, milliseconds) + 1);
  });
}
