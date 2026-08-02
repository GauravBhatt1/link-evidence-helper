import { z } from "zod";

export const verificationStateSchema = z.enum(["unverified", "checking", "verified", "failed", "blocked"]);
export const jobStateSchema = z.enum([
  "queued", "checking-cache", "searching", "checking-preferred-source",
  "checking-backup-source", "browser-fallback", "verified", "partial",
  "blocked", "failed", "cancelled",
]);

export const sourceCandidateSchema = z.object({
  sourceId: z.string().min(1),
  displayName: z.string().min(1),
  priority: z.number().int().min(0),
  verificationState: verificationStateSchema,
}).strict();

export const releaseVariantSchema = z.object({
  variantId: z.string().min(1),
  language: z.string().min(1),
  audioVariant: z.string().min(1),
  quality: z.string().min(1),
  availableQualities: z.array(z.string().min(1)).refine((items) => new Set(items).size === items.length),
  releaseType: z.string().min(1),
  packType: z.enum(["single", "episode", "season", "complete-series"]),
  season: z.number().int().min(0).nullable(),
  episode: z.number().int().min(0).nullable(),
  approxSize: z.string(),
  sourceCount: z.number().int().min(0),
  sources: z.array(sourceCandidateSchema),
}).strict();

export const contentSchema = z.object({
  contentId: z.string().min(1),
  tmdbId: z.string().nullable(),
  title: z.string().min(1),
  year: z.string(),
  mediaType: z.enum(["movie", "tv"]),
  poster: z.string(),
  languages: z.array(z.string().min(1)).refine((items) => new Set(items).size === items.length),
  releaseVariants: z.array(releaseVariantSchema),
  totalSources: z.number().int().min(0),
  jellyfinStatus: z.enum(["available", "missing", "unknown"]),
}).strict();

export const searchResponseSchema = z.object({
  ok: z.literal(true), success: z.literal(true), code: z.literal("ok"), query: z.string(),
  contents: z.array(contentSchema),
  partialFailures: z.array(z.object({sourceId: z.string().min(1), reason: z.string().min(1)}).strict()),
}).strict();

export const resolutionRequestSchema = z.object({
  contentId: z.string().min(1), variantId: z.string().min(1), quality: z.string().min(1).optional(),
}).strict();

export const deliveryLinkSchema = z.object({
  url: z.string().url(), filename: z.string().min(1), size: z.string(), quality: z.string().min(1),
  sourceId: z.string().min(1), verifiedAt: z.string().datetime(),
}).strict();
export const resolutionAttemptSchema = z.object({
  sourceId: z.string().min(1), status: z.enum(["verified", "failed", "blocked", "cancelled"]),
  failureReason: z.string().nullable(), durationMs: z.number().int().min(0),
}).strict();
export const resolutionResultSchema = z.object({
  ok: z.boolean(), success: z.boolean(), code: z.string().min(1),
  status: z.enum(["verified", "partial", "blocked", "failed"]),
  contentId: z.string().min(1), variantId: z.string().min(1),
  deliveryLinks: z.array(deliveryLinkSchema), attempts: z.array(resolutionAttemptSchema), message: z.string(),
}).strict();

export const jobSchema = z.object({
  jobId: z.string().min(1), kind: z.enum(["search", "resolution", "library-scan"]), state: jobStateSchema,
  subscriberCount: z.number().int().min(0), createdAt: z.string().datetime(), updatedAt: z.string().datetime(),
  result: z.record(z.string(), z.unknown()).nullable(),
}).strict();
export const jobEventSchema = z.object({
  eventId: z.string().min(1), jobId: z.string().min(1), state: jobStateSchema, message: z.string(),
  occurredAt: z.string().datetime(), progress: z.number().int().min(0).max(100),
}).strict();
export const errorResponseSchema = z.object({
  ok: z.literal(false), success: z.literal(false), code: z.string().min(1), error: z.string().min(1),
  requestId: z.string().nullable(),
}).strict();

export const zodSchemas = {
  "source-candidate.schema.json": sourceCandidateSchema,
  "release-variant.schema.json": releaseVariantSchema,
  "content.schema.json": contentSchema,
  "search-response.schema.json": searchResponseSchema,
  "resolution-request.schema.json": resolutionRequestSchema,
  "resolution-result.schema.json": resolutionResultSchema,
  "job.schema.json": jobSchema,
  "job-event.schema.json": jobEventSchema,
  "error.schema.json": errorResponseSchema,
} as const;

export type Content = z.infer<typeof contentSchema>;
export type ReleaseVariant = z.infer<typeof releaseVariantSchema>;
export type SourceCandidate = z.infer<typeof sourceCandidateSchema>;
export type SearchResponse = z.infer<typeof searchResponseSchema>;
export type ResolutionRequest = z.infer<typeof resolutionRequestSchema>;
export type ResolutionResult = z.infer<typeof resolutionResultSchema>;
export type Job = z.infer<typeof jobSchema>;
export type JobEvent = z.infer<typeof jobEventSchema>;
export type ErrorResponse = z.infer<typeof errorResponseSchema>;
