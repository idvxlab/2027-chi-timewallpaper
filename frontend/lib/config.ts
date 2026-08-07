/**
 * Centralized URL helpers for the TimeWallpaper frontend.
 *
 * These functions resolve REST, asset, and WebSocket URLs against a single
 * authoritative base configuration so the production deployment can sit
 * behind a single HTTPS domain (via Nginx reverse-proxy) without leaking
 * any hard-coded "localhost" / "127.0.0.1" URLs into the browser bundle,
 * nor any absolute `127.0.0.1:8000` URLs inherited from dev databases.
 *
 * Behavior:
 *
 *   - Server-side / build-time: there is no `window`, so REST helpers
 *     return relative paths starting with `/`. Asset normalization still
 *     runs the URL parser so any string that came in as
 *     `http://localhost:8000/...` or `http://127.0.0.1:8000/...` is
 *     collapsed to the relative form.
 *
 *   - Client-side: when an absolute `NEXT_PUBLIC_API_BASE_URL` is
 *     configured (e.g. local dev pointing at `http://127.0.0.1:8000`),
 *     absolute REST URLs are produced. Otherwise we return same-origin
 *     paths so that the browser hits the same host that served the
 *     frontend (the public domain, behind Nginx).
 *
 *   - WebSocket URLs are auto-derived from `window.location.protocol`:
 *       https page -> "wss://…"
 *       http  page -> "ws://…"
 *     They never embed `ws://localhost`.
 */

/* ── Configuration ─────────────────────────────────────────────────────── */

/**
 * The single source of truth for the backend base. In production this
 * should be unset / empty (so we fall back to same-origin `/api`). For
 * local dev, set it to e.g. `http://127.0.0.1:8000`.
 */
const RAW_API_BASE = (process.env.NEXT_PUBLIC_API_BASE_URL ?? "").trim();

/**
 * True when the configured value is an absolute http(s) URL — used for
 * local development. False (the default in production) means REST
 * helpers return same-origin relative paths.
 */
const IS_ABSOLUTE_API_BASE =
  RAW_API_BASE.startsWith("http://") || RAW_API_BASE.startsWith("https://");

/* ── Helpers ───────────────────────────────────────────────────────────── */

/** Strip trailing slash so we control path concatenation. */
function stripTrailingSlash(value: string): string {
  return value.replace(/\/$/, "");
}

/**
 * The single authoritative backend origin, derived from `NEXT_PUBLIC_API_BASE_URL`.
 *
 *   - `http://127.0.0.1:8000`  → `"http://127.0.0.1:8000"`
 *   - `http://localhost:8000`   → `"http://localhost:8000"`
 *   - `https://api.example.com`  → `"https://api.example.com"`
 *   - `""` / `/api` / unset    → `null`  (production / no absolute base)
 *
 * When non-null, relative asset paths should be resolved against this origin
 * (not the frontend origin) so that browsers in local dev hit port 8000 directly.
 */
function getConfiguredBackendOrigin(): string | null {
  if (!IS_ABSOLUTE_API_BASE) return null;
  try {
    return new URL(RAW_API_BASE).origin;
  } catch {
    return null;
  }
}

/**
 * Returns the current page origin (e.g. `https://app.example.com`) when
 * running in the browser, or `""` during SSR / build. Centralizing this
 * lets asset normalization recognize "this deployment's own origin"
 * regardless of which file calls it.
 */
function currentBrowserOrigin(): string {
  if (typeof window === "undefined") return "";
  return window.location.origin;
}

/**
 * Decide whether a parsed URL's host is "local". We treat loopback
 * (`localhost`) and IPv4 loopback (`127.0.0.0/8`) as local, plus any
 * unspecified / IPv6 loopback (`::1`). Everything else is treated as
 * external and only allowed through `assetUrl` if the operator stored it
 * deliberately (CDN, etc.).
 */
function isLocalHost(hostname: string): boolean {
  const h = hostname.toLowerCase();
  if (h === "localhost" || h === "::1" || h === "[::1]") return true;
  if (h.startsWith("127.")) return true;
  return false;
}

/**
 * Normalize an asset path so it never leaks `localhost` / `127.0.0.1`
 * URLs into the browser bundle, even if those came from legacy DB rows.
 *
 * Rules (all parsed with `new URL(...)` rather than a regex on the raw
 * string so port / casing / encoding can't fool us):
 *
 *   1. Empty / falsy          -> "" (no-op).
 *
 *   2. Relative path ("/gen/x")
 *      - dev (absolute `NEXT_PUBLIC_API_BASE_URL`): prefix with backend origin
 *        so the browser fetches from port 8000 instead of the Next.js port.
 *      - production (no absolute base): returned unchanged; Nginx proxies it.
 *
 *   3. Absolute URL whose host
 *      - is loopback (localhost / 127.x):
 *          dev: rewrite to the configured backend origin (avoids localhost/127.x mixing).
 *          prod: collapse to relative path.
 *      - equals the current browser origin: collapsed to relative path.
 *      - is anything else (CDN, etc.): returned unchanged.
 *
 *   4. Anything that can't be parsed: returned unchanged.
 *
 * Same-origin absolute URLs are *only* stripped on the client (because
 * that's where we know the page's origin). During SSR we keep them
 * absolute; the client will see them, recognize them, and the next
 * render will collapse them.
 */
export function assetUrl(path: string): string {
  if (!path) return "";

  // Already relative — fast-path.
  if (path.startsWith("/")) {
    const backendOrigin = getConfiguredBackendOrigin();
    if (backendOrigin) {
      // Dev: prefix with the backend origin so the browser fetches from
      // port 8000 directly. e.g. /generated/x.png → http://127.0.0.1:8000/generated/x.png
      return backendOrigin + path;
    }
    // Production: let Nginx proxy the relative path.
    return path;
  }

  // Try to parse. If parsing fails we have to leave the value alone so
  // we don't corrupt data; the caller is responsible for passing valid
  // input.
  let parsed: URL;
  try {
    parsed = new URL(path);
  } catch {
    return path;
  }

  // Local host? Resolve to the configured backend origin in dev, or
  // collapse to relative in production. This normalizes the case where
  // DB rows contain `http://localhost:8000/...` but the operator
  // configured `NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000`.
  if (isLocalHost(parsed.hostname)) {
    const backendOrigin = getConfiguredBackendOrigin();
    if (backendOrigin) {
      // Rewrite localhost / 127.x to the configured backend origin.
      return backendOrigin + parsed.pathname + parsed.search + parsed.hash;
    }
    return parsed.pathname + parsed.search + parsed.hash;
  }

  // On the client, collapse same-origin absolute URLs to relative. The
  // server doesn't know the page origin (and the result is rendered
  // into HTML the client will normalize anyway), so keep them absolute
  // during SSR.
  const pageOrigin = currentBrowserOrigin();
  if (pageOrigin && parsed.origin === pageOrigin) {
    return parsed.pathname + parsed.search + parsed.hash;
  }

  // External host (CDN, etc.) — pass through. Operator chose to store an
  // absolute URL on purpose.
  return path;
}

/**
 * REST endpoint resolver.
 *
 * Behavior:
 *
 *   - Relative paths (`/api`, `/api/foo/bar`) are returned unchanged.
 *   - A leading slash is ensured for plain `path` values.
 *   - In production (no `NEXT_PUBLIC_API_BASE_URL`) the path is treated
 *     as same-origin: `/api/foo/bar` for input `foo/bar`.
 *
 * @param path API path, e.g. `/wallpapers/current` or `wallpapers/current`.
 * @returns Absolute URL (dev) or same-origin relative path (prod).
 */
export function apiUrl(path: string): string {
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;

  if (IS_ABSOLUTE_API_BASE) {
    return `${stripTrailingSlash(RAW_API_BASE)}${normalizedPath}`;
  }

  // Production same-origin mode: Nginx exposes REST endpoints under
  // `/api/*` and strips that prefix before forwarding to FastAPI.
  return `/api${normalizedPath}`;
}

/**
 * Build a WebSocket URL from a relative path. Auto-selects `wss://` for
 * HTTPS pages and `ws://` for HTTP pages, and uses same-origin by
 * default.
 *
 * Example:
 *   websocketUrl("/wallpapers/current/events")
 *   prod, https page  -> "wss://app.example.com/wallpapers/current/events"
 *   dev, http page    -> "ws://127.0.0.1:8000/wallpapers/current/events"
 */
export function websocketUrl(path: string): string {
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;

  if (typeof window === "undefined") {
    // Server-side: can't infer protocol. Return a relative path that the
    // browser will resolve against whatever host the page is served
    // from, using the same `wss`/`ws` upgrade rules.
    return normalizedPath;
  }

  const wsProtocol = window.location.protocol === "https:" ? "wss:" : "ws:";

  if (IS_ABSOLUTE_API_BASE) {
    // dev: absolute http://host:port — use it as the WS base.
    const apiBaseUrl = new URL(RAW_API_BASE);
    return `${wsProtocol}//${apiBaseUrl.host}${normalizedPath}`;
  }

  // prod: same-origin.
  return `${wsProtocol}//${window.location.host}${normalizedPath}`;
}

/* ── Convenience: the public API base, for code paths that need it. ──── */

/**
 * Returns the raw configured base URL if absolute, otherwise empty
 * string. Useful for introspection / logging — prefer `apiUrl(path)`
 * when building concrete endpoints.
 */
export function rawApiBase(): string {
  return IS_ABSOLUTE_API_BASE ? stripTrailingSlash(RAW_API_BASE) : "";
}
