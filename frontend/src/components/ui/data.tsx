"use client";
import { CheckCircle2, CircleDashed, Loader2, MinusCircle, OctagonAlert, TriangleAlert, X } from "lucide-react";
import { ReactNode } from "react";
import { Icon, IconComponent } from "./Icon";

export function MetricCard({
  label,
  value,
  hint,
  icon,
}: {
  label: string;
  value: ReactNode;
  hint?: string;
  icon?: IconComponent;
}) {
  return (
    <div className="card p-4">
      <div className="flex items-center justify-between gap-2 text-sm text-muted">
        {label}
        {icon && <Icon icon={icon} className="text-subtle" />}
      </div>
      <div className="mt-1.5 text-2xl font-semibold tabular-nums text-fg">{value}</div>
      {hint && <div className="mt-1 text-xs text-subtle">{hint}</div>}
    </div>
  );
}

type Tone = "ok" | "warn" | "danger" | "info" | "neutral";

const BADGE: Record<Tone, { box: string; icon: IconComponent }> = {
  ok: { box: "bg-ok-soft text-ok", icon: CheckCircle2 },
  warn: { box: "bg-warn-soft text-warn", icon: TriangleAlert },
  danger: { box: "bg-danger-soft text-danger", icon: OctagonAlert },
  info: { box: "bg-info-soft text-info", icon: Loader2 },
  neutral: { box: "bg-sunken text-muted", icon: CircleDashed },
};

const STATUS_TONE: Record<string, Tone> = {
  healthy: "ok",
  completed: "ok",
  ok: "ok",
  exporting: "ok",
  active: "ok",
  processing: "info",
  running: "info",
  queued: "neutral",
  degraded: "warn",
  failing: "warn",
  denied: "warn",
  disabled: "neutral",
  failed: "danger",
  unhealthy: "danger",
  error: "danger",
};

/** State, never decoration: the icon repeats what the colour says, so colour is not the only signal. */
export function StatusBadge({ status, tone }: { status: string; tone?: Tone }) {
  const resolved = tone ?? STATUS_TONE[status?.toLowerCase()] ?? "neutral";
  const { box, icon } = BADGE[resolved];
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ${box}`}>
      <Icon icon={status?.toLowerCase() === "disabled" ? MinusCircle : icon} size="sm" />
      {status}
    </span>
  );
}

export function HealthIndicator({ status }: { status: string }) {
  const key = status?.toLowerCase();
  const color = ["healthy", "ok", "exporting"].includes(key)
    ? "bg-ok"
    : key === "disabled"
      ? "bg-line-strong"
      : key === "failing"
        ? "bg-warn"
        : "bg-danger";
  return (
    <span className={`inline-block h-2.5 w-2.5 rounded-full ${color}`}>
      <span className="sr-only">{status}</span>
    </span>
  );
}

type ChipTone = "neutral" | "accent" | "clay";

const CHIP: Record<ChipTone, string> = {
  neutral: "border-line-strong bg-sunken/60 text-fg",
  accent: "border-accent/25 bg-accent-soft text-accent-text",
  clay: "border-clay/30 bg-clay-soft text-clay-text",
};

/** A small typed token: a Principal, a tag, a filter. Removable when given `onRemove`. */
export function Chip({
  children,
  tone = "neutral",
  icon,
  onRemove,
  removeLabel,
}: {
  children: ReactNode;
  tone?: ChipTone;
  icon?: IconComponent;
  onRemove?: () => void;
  removeLabel?: string;
}) {
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-control border px-2 py-0.5 text-xs font-medium ${CHIP[tone]}`}>
      {icon && <Icon icon={icon} size="sm" />}
      {children}
      {onRemove && (
        <button
          type="button"
          onClick={onRemove}
          aria-label={removeLabel ?? `Remove ${typeof children === "string" ? children : "item"}`}
          className="-mr-1 rounded-sm p-0.5 hover:bg-fg/10"
        >
          <Icon icon={X} size="sm" />
        </button>
      )}
    </span>
  );
}

// ---- Table ---------------------------------------------------------------------------------
// Wide tables scroll inside their own container, so a narrow screen never breaks the page layout.

export function Table({ children, caption }: { children: ReactNode; caption?: string }) {
  return (
    <div className="relative overflow-x-auto rounded-panel border border-line bg-surface">
      <table className="w-full text-sm">
        {caption && <caption className="sr-only">{caption}</caption>}
        {children}
      </table>
    </div>
  );
}

export const THead = ({ children }: { children: ReactNode }) => (
  <thead className="border-b border-line bg-sunken/50 text-left">{children}</thead>
);

export const TBody = ({ children }: { children: ReactNode }) => <tbody className="divide-y divide-line">{children}</tbody>;

export function Tr({ children, onClick }: { children: ReactNode; onClick?: () => void }) {
  return (
    <tr onClick={onClick} className={onClick ? "cursor-pointer hover:bg-sunken/50" : "hover:bg-sunken/30"}>
      {children}
    </tr>
  );
}

export const Th = ({ children, align = "left" }: { children?: ReactNode; align?: "left" | "right" }) => (
  <th scope="col" className={`whitespace-nowrap px-4 py-2.5 text-xs font-medium text-muted ${align === "right" ? "text-right" : "text-left"}`}>
    {children}
  </th>
);

export const Td = ({
  children,
  align = "left",
  mono = false,
}: {
  children?: ReactNode;
  align?: "left" | "right";
  mono?: boolean;
}) => (
  <td className={`px-4 py-2.5 text-fg ${align === "right" ? "whitespace-nowrap text-right tabular-nums" : ""} ${mono ? "font-mono text-xs" : ""}`}>
    {children}
  </td>
);
