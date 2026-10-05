/**
 * supabase-auth.js — browser-side auth helpers for Sign2Text.
 *
 * PUBLIC-REPOSITORY NOTE
 * ----------------------
 * The implementation used in the private project was written by Stepan
 * Karapetyan and is not mine to publish. This is an independently written,
 * interface-compatible replacement so the frontend modules in this repository
 * remain functional.
 *
 * Exports the contract app.js depends on:
 *   getAuthToken()          -> Promise<string|null>
 *   getAuthHeaders(extra)   -> Promise<Object>
 *   authFetch(url, options) -> Promise<Response>
 *   buildSseUrl(baseUrl)    -> Promise<string>
 *
 * The Supabase client is created lazily from window.APP_CONFIG, which start.py
 * generates into runtime-config.js. When Supabase is not configured the helpers
 * degrade to unauthenticated requests so the app still runs locally.
 */

let _clientPromise = null;

function config() {
  return (typeof window !== 'undefined' && window.APP_CONFIG) || {};
}

function isEnabled() {
  const cfg = config();
  return Boolean(cfg.SUPABASE_URL && cfg.SUPABASE_ANON_KEY);
}

/** Lazily create a single shared Supabase client. */
function getClient() {
  if (!isEnabled()) return Promise.resolve(null);
  if (_clientPromise) return _clientPromise;

  _clientPromise = Promise.resolve().then(() => {
    // supabase-js is loaded as a UMD bundle by index.html.
    const lib = typeof window !== 'undefined' ? window.supabase : null;
    if (!lib || typeof lib.createClient !== 'function') {
      console.warn('[auth] supabase-js not loaded; continuing unauthenticated');
      return null;
    }
    const cfg = config();
    return lib.createClient(cfg.SUPABASE_URL, cfg.SUPABASE_ANON_KEY);
  });

  return _clientPromise;
}

/** Current access token, or null when signed out / Supabase disabled. */
export async function getAuthToken() {
  const client = await getClient();
  if (!client) return null;
  try {
    const { data, error } = await client.auth.getSession();
    if (error) {
      console.warn('[auth] getSession failed:', error.message);
      return null;
    }
    return data?.session?.access_token ?? null;
  } catch (err) {
    console.warn('[auth] getSession threw:', err);
    return null;
  }
}

/** Merge an Authorization header into `extra` when a session exists. */
export async function getAuthHeaders(extra = {}) {
  const token = await getAuthToken();
  return token ? { ...extra, Authorization: `Bearer ${token}` } : { ...extra };
}

/** fetch() with the Authorization header attached when available. */
export async function authFetch(url, options = {}) {
  const headers = await getAuthHeaders(options.headers || {});
  return fetch(url, { ...options, headers });
}

/**
 * Append the access token as a query parameter.
 *
 * The browser EventSource and WebSocket APIs cannot set custom headers, so the
 * backend accepts `?token=` as a documented fallback for those transports only.
 */
export async function buildSseUrl(baseUrl) {
  const token = await getAuthToken();
  if (!token) return baseUrl;
  const separator = baseUrl.includes('?') ? '&' : '?';
  return `${baseUrl}${separator}token=${encodeURIComponent(token)}`;
}
