// Which part of the UI each role reaches. The API enforces this on the server (querier -> chat,
// admin -> control plane, auditor -> audit log); this only keeps the UI from showing a user pages
// that would answer 403, and sends them to the surface they do have.

export type Role = "admin" | "querier" | "auditor";

/** The role a path needs. Admin pages are the default so a new page is never shown to employees by accident. */
export function roleForPath(pathname: string): Role | null {
  if (pathname.startsWith("/auth/")) return null; // the OIDC callback
  if (pathname === "/chat" || pathname.startsWith("/chat/")) return "querier";
  if (pathname === "/audit" || pathname.startsWith("/audit/")) return "auditor";
  return "admin";
}

/** Where a signed-in user with these roles should land. */
export function homeFor(roles: Role[]): string | null {
  if (roles.includes("admin")) return "/";
  if (roles.includes("querier")) return "/chat";
  if (roles.includes("auditor")) return "/audit";
  return null;
}
