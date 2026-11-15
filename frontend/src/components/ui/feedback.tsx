"use client";
import { AlertTriangle, CheckCircle2, Info, OctagonAlert, RotateCw, X } from "lucide-react";
import { ReactNode, createContext, useCallback, useContext, useEffect, useState } from "react";
import { Icon, IconComponent } from "./Icon";

/** A placeholder shaped like what is loading. Decorative: the surrounding state names it. */
export function Skeleton({ className = "" }: { className?: string }) {
  return <div data-skeleton aria-hidden className={`skeleton ${className}`} />;
}

export function SkeletonText({ lines = 3, className = "" }: { lines?: number; className?: string }) {
  return (
    <div className={`space-y-2.5 ${className}`}>
      {Array.from({ length: lines }, (_, i) => (
        <Skeleton key={i} className={`h-3.5 ${i === lines - 1 && lines > 1 ? "w-2/3" : "w-full"}`} />
      ))}
    </div>
  );
}

/** Rows shaped like a table that is loading, so the page does not jump when the data lands. */
export function TableSkeleton({ rows = 4, columns = 4, label = "Loading…" }: { rows?: number; columns?: number; label?: string }) {
  return (
    <div role="status" className="overflow-hidden rounded-panel border border-line bg-surface">
      <span className="sr-only">{label}</span>
      <div className="border-b border-line bg-sunken/50 px-4 py-3">
        <Skeleton className="h-3 w-1/4" />
      </div>
      {Array.from({ length: rows }, (_, r) => (
        <div key={r} className="flex gap-6 border-b border-line px-4 py-3.5 last:border-0">
          {Array.from({ length: columns }, (_, c) => (
            <Skeleton key={c} className={`h-4 ${c === 0 ? "w-1/4" : "flex-1"}`} />
          ))}
        </div>
      ))}
    </div>
  );
}

export function LoadingState({ label = "Loading…" }: { label?: string }) {
  return (
    <div role="status" className="py-8">
      <span className="sr-only">{label}</span>
      <SkeletonText lines={4} />
    </div>
  );
}

export function ErrorState({
  message,
  title = "Something went wrong",
  onRetry,
}: {
  message: string;
  title?: string;
  onRetry?: () => void;
}) {
  return (
    <div role="alert" className="rounded-panel border border-danger/30 bg-danger-soft p-5 text-sm text-danger">
      <div className="flex items-center gap-2 font-medium">
        <Icon icon={OctagonAlert} />
        {title}
      </div>
      <p className="mt-1.5 text-fg">{message}</p>
      {onRetry && (
        <button type="button" className="btn btn-secondary btn-sm mt-3" onClick={onRetry}>
          <Icon icon={RotateCw} size="sm" />
          Try again
        </button>
      )}
    </div>
  );
}

export function EmptyState({
  title,
  description,
  action,
  icon,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
  icon?: IconComponent;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 rounded-panel border border-dashed border-line-strong px-6 py-14 text-center">
      {icon && <Icon icon={icon} size="xl" className="text-subtle" />}
      <div className="font-display text-lg font-semibold text-fg">{title}</div>
      {description && <p className="max-w-sm text-sm text-muted">{description}</p>}
      {action}
    </div>
  );
}

const CALLOUT: Record<string, { box: string; icon: IconComponent }> = {
  info: { box: "border-line-strong bg-sunken/60", icon: Info },
  warn: { box: "border-clay/30 bg-clay-soft", icon: AlertTriangle },
  danger: { box: "border-danger/30 bg-danger-soft", icon: OctagonAlert },
  ok: { box: "border-ok/30 bg-ok-soft", icon: CheckCircle2 },
};

const CALLOUT_ICON_COLOR: Record<string, string> = {
  info: "text-accent-text",
  warn: "text-clay-text",
  danger: "text-danger",
  ok: "text-ok",
};

export function Callout({
  tone = "info",
  title,
  children,
}: {
  tone?: "info" | "warn" | "danger" | "ok";
  title?: string;
  children: ReactNode;
}) {
  const { box, icon } = CALLOUT[tone];
  return (
    <div className={`flex gap-3 rounded-panel border p-3.5 text-sm text-fg ${box}`}>
      <Icon icon={icon} className={`mt-0.5 ${CALLOUT_ICON_COLOR[tone]}`} />
      <div>
        {title && <div className="font-medium">{title}</div>}
        <div className={title ? "mt-0.5 text-muted" : ""}>{children}</div>
      </div>
    </div>
  );
}

// ---- Toasts --------------------------------------------------------------------------------

type ToastTone = "ok" | "danger" | "info";
interface Toast {
  id: number;
  title: string;
  description?: string;
  tone: ToastTone;
}

const ToastContext = createContext<((toast: Omit<Toast, "id" | "tone"> & { tone?: ToastTone }) => void) | null>(null);

/** Brief confirmation of something the person just did. Errors stay longer. */
export function useToast() {
  const push = useContext(ToastContext);
  if (!push) throw new Error("useToast needs a ToastProvider above it");
  return push;
}

const TOAST_ICON: Record<ToastTone, { icon: IconComponent; color: string }> = {
  ok: { icon: CheckCircle2, color: "text-ok" },
  danger: { icon: OctagonAlert, color: "text-danger" },
  info: { icon: Info, color: "text-accent-text" },
};

function ToastItem({ toast, onDismiss }: { toast: Toast; onDismiss: (id: number) => void }) {
  const { id, tone } = toast;
  useEffect(() => {
    const timer = setTimeout(() => onDismiss(id), tone === "danger" ? 9000 : 5000);
    return () => clearTimeout(timer);
  }, [id, tone, onDismiss]);
  const { icon, color } = TOAST_ICON[toast.tone];
  return (
    <div
      role="status"
      className="pointer-events-auto flex w-80 max-w-full animate-rise-in items-start gap-3 rounded-panel border border-line bg-surface p-3.5 text-sm text-fg shadow-overlay"
    >
      <Icon icon={icon} className={`mt-0.5 ${color}`} />
      <div className="min-w-0 flex-1">
        <div className="font-medium">{toast.title}</div>
        {toast.description && <div className="mt-0.5 text-muted">{toast.description}</div>}
      </div>
      <button type="button" onClick={() => onDismiss(id)} aria-label="Dismiss" className="btn btn-ghost btn-sm -mr-1 -mt-1">
        <Icon icon={X} size="sm" />
      </button>
    </div>
  );
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const push = useCallback((toast: Omit<Toast, "id" | "tone"> & { tone?: ToastTone }) => {
    setToasts((all) => [...all, { ...toast, tone: toast.tone ?? "ok", id: Date.now() + Math.random() }]);
  }, []);
  const dismiss = useCallback((id: number) => setToasts((all) => all.filter((t) => t.id !== id)), []);
  return (
    <ToastContext.Provider value={push}>
      {children}
      <div className="pointer-events-none fixed bottom-4 right-4 z-[60] flex flex-col gap-2">
        {toasts.map((t) => (
          <ToastItem key={t.id} toast={t} onDismiss={dismiss} />
        ))}
      </div>
    </ToastContext.Provider>
  );
}
