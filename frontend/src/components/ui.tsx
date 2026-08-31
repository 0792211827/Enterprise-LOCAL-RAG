"use client";
import { ReactNode, useEffect, useState } from "react";

export function PageHeader({
  title,
  description,
  action,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="mb-6 flex items-start justify-between gap-4">
      <div>
        <h1 className="text-2xl font-semibold text-slate-900">{title}</h1>
        {description && <p className="mt-1 text-sm text-slate-500">{description}</p>}
      </div>
      {action}
    </div>
  );
}

export function MetricCard({
  label,
  value,
  hint,
}: {
  label: string;
  value: ReactNode;
  hint?: string;
}) {
  return (
    <div className="card p-5">
      <div className="text-sm font-medium text-slate-500">{label}</div>
      <div className="mt-2 text-3xl font-semibold text-slate-900">{value}</div>
      {hint && <div className="mt-1 text-xs text-slate-400">{hint}</div>}
    </div>
  );
}

const STATUS_STYLES: Record<string, string> = {
  healthy: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  completed: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  ok: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  processing: "bg-amber-50 text-amber-700 ring-amber-200",
  queued: "bg-slate-100 text-slate-600 ring-slate-200",
  degraded: "bg-amber-50 text-amber-700 ring-amber-200",
  disabled: "bg-slate-100 text-slate-500 ring-slate-200",
  failed: "bg-red-50 text-red-700 ring-red-200",
  unhealthy: "bg-red-50 text-red-700 ring-red-200",
};

export function StatusBadge({ status }: { status: string }) {
  const key = status?.toLowerCase();
  const cls = STATUS_STYLES[key] || "bg-slate-100 text-slate-600 ring-slate-200";
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset ${cls}`}
    >
      <span className="h-1.5 w-1.5 rounded-full bg-current opacity-70" />
      {status}
    </span>
  );
}

export function HealthIndicator({ status }: { status: string }) {
  const healthy = ["healthy", "ok"].includes(status?.toLowerCase());
  const disabled = status?.toLowerCase() === "disabled";
  const color = healthy ? "bg-emerald-500" : disabled ? "bg-slate-300" : "bg-red-500";
  return <span className={`inline-block h-2.5 w-2.5 rounded-full ${color}`} />;
}

export function LoadingState({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-3 py-16 text-sm text-slate-400">
      <span className="h-4 w-4 animate-spin rounded-full border-2 border-slate-300 border-t-brand-500" />
      {label}
    </div>
  );
}

export function ErrorState({ message }: { message: string }) {
  return (
    <div className="card border-red-200 bg-red-50 p-6 text-sm text-red-700">
      <div className="font-medium">Something went wrong</div>
      <p className="mt-1 text-red-600">{message}</p>
    </div>
  );
}

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="card flex flex-col items-center justify-center gap-3 border-dashed py-16 text-center">
      <div className="text-base font-medium text-slate-700">{title}</div>
      {description && <p className="max-w-sm text-sm text-slate-400">{description}</p>}
      {action}
    </div>
  );
}

const MODAL_SIZES: Record<string, string> = {
  md: "max-w-lg",
  lg: "max-w-2xl",
  xl: "max-w-4xl",
};

export function Modal({
  open,
  onClose,
  title,
  children,
  size = "md",
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  size?: "md" | "lg" | "xl";
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4"
      // Only a click on the backdrop itself closes; a drag that ends here
      // shouldn't discard whatever the user was typing.
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className={`card w-full ${MODAL_SIZES[size]} p-6`}>
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-slate-900">{title}</h2>
          <button
            onClick={onClose}
            className="text-slate-400 hover:text-slate-600"
            aria-label="Close"
          >
            ✕
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

export function confirmDialog(message: string): boolean {
  if (typeof window === "undefined") return false;
  return window.confirm(message);
}


/** Step indicator for multi-step flows. Completed steps are clickable. */
export function Stepper({
  steps,
  current,
  onStepClick,
}: {
  steps: string[];
  current: number;
  onStepClick?: (index: number) => void;
}) {
  return (
    <ol className="mb-8 flex items-center gap-2">
      {steps.map((label, i) => {
        const done = i < current;
        const active = i === current;
        const clickable = done && onStepClick;
        return (
          <li key={label} className="flex flex-1 items-center gap-2">
            <button
              type="button"
              disabled={!clickable}
              onClick={() => clickable && onStepClick(i)}
              className={`flex items-center gap-2 text-sm ${
                clickable ? "cursor-pointer" : "cursor-default"
              }`}
            >
              <span
                className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-xs font-semibold ring-1 ring-inset ${
                  active
                    ? "bg-brand-600 text-white ring-brand-600"
                    : done
                      ? "bg-brand-50 text-brand-700 ring-brand-200"
                      : "bg-white text-slate-400 ring-slate-200"
                }`}
              >
                {done ? "✓" : i + 1}
              </span>
              <span
                className={
                  active
                    ? "font-medium text-slate-900"
                    : done
                      ? "text-slate-600"
                      : "text-slate-400"
                }
              >
                {label}
              </span>
            </button>
            {i < steps.length - 1 && (
              <span
                className={`hidden h-px flex-1 sm:block ${done ? "bg-brand-200" : "bg-slate-200"}`}
              />
            )}
          </li>
        );
      })}
    </ol>
  );
}

/**
 * Copy-to-clipboard with a fallback.
 *
 * `navigator.clipboard` is undefined on non-secure origins, and this admin app
 * is served over plain HTTP — so an admin reaching the box by LAN IP rather
 * than localhost would otherwise get a button that silently does nothing.
 */
export function CopyButton({
  value,
  label = "Copy",
  className = "",
}: {
  value: string;
  label?: string;
  className?: string;
}) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");

  const copy = async () => {
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(value);
      } else {
        const ta = document.createElement("textarea");
        ta.value = value;
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.select();
        const ok = document.execCommand("copy");
        document.body.removeChild(ta);
        if (!ok) throw new Error("execCommand failed");
      }
      setState("copied");
    } catch {
      setState("failed");
    }
    setTimeout(() => setState("idle"), 2000);
  };

  return (
    <button type="button" onClick={copy} className={`btn btn-secondary ${className}`}>
      {state === "copied" ? "Copied" : state === "failed" ? "Copy failed" : label}
    </button>
  );
}

export function CodeBlock({ code, action }: { code: string; action?: ReactNode }) {
  return (
    <div className="relative">
      <pre className="overflow-x-auto rounded-lg bg-slate-900 p-4 text-xs leading-relaxed text-slate-100">
        <code>{code}</code>
      </pre>
      {action && <div className="absolute right-2 top-2">{action}</div>}
    </div>
  );
}

export function Tabs({
  tabs,
  value,
  onChange,
}: {
  tabs: { id: string; label: string }[];
  value: string;
  onChange: (id: string) => void;
}) {
  return (
    <div className="mb-3 flex gap-1 border-b border-slate-200">
      {tabs.map((t) => (
        <button
          key={t.id}
          type="button"
          onClick={() => onChange(t.id)}
          className={`-mb-px border-b-2 px-3 py-2 text-sm font-medium transition ${
            value === t.id
              ? "border-brand-600 text-brand-700"
              : "border-transparent text-slate-500 hover:text-slate-700"
          }`}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}

const CALLOUT_TONES: Record<string, string> = {
  info: "border-brand-100 bg-brand-50 text-brand-700",
  warn: "border-amber-200 bg-amber-50 text-amber-800",
};

export function Callout({
  tone = "info",
  title,
  children,
}: {
  tone?: "info" | "warn";
  title?: string;
  children: ReactNode;
}) {
  return (
    <div className={`rounded-lg border p-3 text-sm ${CALLOUT_TONES[tone]}`}>
      {title && <div className="font-medium">{title}</div>}
      <div className={title ? "mt-1" : ""}>{children}</div>
    </div>
  );
}

/** Native <details> styled to match; zero JS and keyboard-accessible for free. */
export function Advanced({
  title = "Advanced",
  children,
}: {
  title?: string;
  children: ReactNode;
}) {
  return (
    <details className="rounded-lg border border-slate-200 bg-slate-50/60 px-4 py-3">
      <summary className="cursor-pointer select-none text-sm font-medium text-slate-700">
        {title}
      </summary>
      <div className="mt-4 space-y-4">{children}</div>
    </details>
  );
}

export function SecretReveal({ value, masked }: { value?: string | null; masked: string }) {
  const [shown, setShown] = useState(false);
  return (
    <div className="flex items-center gap-2">
      <code className="rounded bg-slate-100 px-2 py-1 font-mono text-xs text-slate-700">
        {shown && value ? value : masked}
      </code>
      {value && (
        <button
          type="button"
          onClick={() => setShown((v) => !v)}
          className="text-xs text-brand-600 hover:underline"
        >
          {shown ? "Hide" : "Reveal"}
        </button>
      )}
      {value && <CopyButton value={value} className="!px-2 !py-1 !text-xs" />}
    </div>
  );
}
