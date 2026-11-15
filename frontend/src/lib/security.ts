import type { SecurityStatus } from "./api/types";

/** True when a Knowledge Base with no Grants cannot be read by signed-in callers: authentication is on and ungranted access is denied. */
export const deniesUngranted = (s: SecurityStatus | undefined): boolean => !!s && s.auth_mode !== "disabled" && s.ungranted_kb_access === "deny";
