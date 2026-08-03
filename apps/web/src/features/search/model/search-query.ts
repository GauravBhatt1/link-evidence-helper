export const MAX_SEARCH_QUERY_LENGTH = 120;

export function normalizeSearchQuery(value: string) {
  return value.trim().replace(/\s+/g, " ");
}

export function fixtureAliasKey(value: string) {
  return normalizeSearchQuery(value).toLocaleLowerCase("en-US");
}

export function validateSearchQuery(value: string) {
  const normalized = normalizeSearchQuery(value);
  if (!normalized) return { ok: false as const, error: "Enter a title to search." };
  if (normalized.length > MAX_SEARCH_QUERY_LENGTH) {
    return { ok: false as const, error: `Search must be ${MAX_SEARCH_QUERY_LENGTH} characters or fewer.` };
  }
  return { ok: true as const, query: normalized };
}
