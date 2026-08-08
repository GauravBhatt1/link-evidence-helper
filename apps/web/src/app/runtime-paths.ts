function normalizedPrefix(value: string | undefined) {
  const trimmed = (value ?? "").trim();
  if (!trimmed || trimmed === "/") return "";
  return `/${trimmed.replace(/^\/+|\/+$/g, "")}`;
}

export const apiBasePath = normalizedPrefix(import.meta.env.VITE_API_BASE_PATH);

export function apiPath(path: string) {
  const suffix = path.startsWith("/") ? path : `/${path}`;
  return `${apiBasePath}${suffix}`;
}

export function publicPath(path: string) {
  const base = import.meta.env.BASE_URL === "/" ? "" : import.meta.env.BASE_URL.replace(/\/$/, "");
  const suffix = path.startsWith("/") ? path : `/${path}`;
  return `${base}${suffix}`;
}
