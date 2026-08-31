/**
 * Mirrors `backend/src/services/slug.py::slugify` exactly — keep in sync.
 *
 * Deliberately does NOT strip accents. The backend doesn't either, so "Café"
 * becomes "caf" server-side; a "smarter" frontend would show a slug preview
 * that doesn't match the model name the API actually assigns.
 */
export function slugify(value: string): string {
  let v = value.trim().toLowerCase();
  v = v.replace(/[^a-z0-9]+/g, "-");
  v = v.replace(/-{2,}/g, "-").replace(/^-+|-+$/g, "");
  return v || "item";
}
