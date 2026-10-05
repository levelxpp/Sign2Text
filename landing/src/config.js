/**
 * App target URL for the "Try the App" button.
 * In dev mode (npm run dev) uses the absolute local URL so the button works
 * when the landing dev server runs separately from the main app.
 * In production builds the relative path works for any origin (local, tunnel, proxy).
 */
export const APP_URL = import.meta.env.DEV
  ? 'http://127.0.0.1:5500/index.html'
  : '/index.html';

export const ADMIN_API_URL = import.meta.env.DEV
  ? 'http://127.0.0.1:8003'
  : '/admin-api';
