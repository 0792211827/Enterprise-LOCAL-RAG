import type { Connector } from "@/lib/api/types";

export const ENV_REFERENCE = /^env:[A-Za-z_][A-Za-z0-9_]*$/;
export const CONNECTOR_NAME = /^[A-Za-z0-9][A-Za-z0-9._-]*$/;

export type Health = { label: string; tone: "ok" | "warn" | "danger" | "info" | "neutral"; failed: boolean };

/** How a Connector is doing, from its last run. `failed` marks the ones that need an Admin's attention. */
export function connectorHealth(c: Connector): Health {
  if (c.running) return { label: "Running", tone: "info", failed: false };
  if (c.last_run_status === "error") return { label: "Failed", tone: "danger", failed: true };
  if (c.last_run_status === "partial") return { label: "Some files failed", tone: "warn", failed: false };
  if (c.last_run_status === "ok") return { label: "Healthy", tone: "ok", failed: false };
  return { label: "Not run yet", tone: "neutral", failed: false };
}

/** The message a failed run left behind, when it left one. */
export function lastError(c: Connector): string | null {
  const error = c.last_run_summary?.error;
  return typeof error === "string" && error ? error : null;
}

export function describeSource(c: Connector): { primary: string; secondary?: string } {
  if (c.source_type === "s3") {
    const prefix = c.source.prefix ? `/${c.source.prefix.replace(/\/$/, "")}` : "";
    return { primary: `s3://${c.source.bucket ?? ""}${prefix}`, secondary: c.source.endpoint_url };
  }
  return { primary: c.source.path ?? "—" };
}
