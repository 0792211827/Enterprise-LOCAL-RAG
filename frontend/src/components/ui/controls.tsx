"use client";
import { Check, ChevronDown, Copy, Eye, EyeOff } from "lucide-react";
import { KeyboardEvent, ReactElement, ReactNode, cloneElement, useId, useState } from "react";
import { Icon } from "./Icon";

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
              aria-current={active ? "step" : undefined}
              onClick={() => clickable && onStepClick(i)}
              className={`flex items-center gap-2 text-sm ${clickable ? "cursor-pointer" : "cursor-default"}`}
            >
              <span
                className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-xs font-semibold ring-1 ring-inset ${
                  active
                    ? "bg-accent text-accent-fg ring-accent"
                    : done
                      ? "bg-accent-soft text-accent-text ring-accent/30"
                      : "bg-surface text-subtle ring-line-strong"
                }`}
              >
                {done ? <Icon icon={Check} size="sm" /> : i + 1}
              </span>
              <span className={active ? "font-medium text-fg" : done ? "text-muted" : "text-subtle"}>{label}</span>
            </button>
            {i < steps.length - 1 && <span className={`hidden h-px flex-1 sm:block ${done ? "bg-accent/40" : "bg-line"}`} />}
          </li>
        );
      })}
    </ol>
  );
}

/**
 * Copy-to-clipboard with a fallback.
 *
 * `navigator.clipboard` is undefined on non-secure origins, and this app
 * is served over plain HTTP — so someone reaching the box by LAN IP rather
 * than localhost would otherwise get a button that silently does nothing.
 */
export function CopyButton({
  value,
  label = "Copy",
  className = "",
  quiet = false,
}: {
  value: string;
  label?: string;
  className?: string;
  /** A small borderless button, for an action that sits inside content. */
  quiet?: boolean;
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
    <button type="button" onClick={copy} className={`btn ${quiet ? "btn-ghost btn-sm" : "btn-secondary"} ${className}`}>
      <Icon icon={state === "copied" ? Check : Copy} size="sm" />
      <span aria-live="polite">{state === "copied" ? "Copied" : state === "failed" ? "Copy failed" : label}</span>
    </button>
  );
}

export function CodeBlock({ code, action }: { code: string; action?: ReactNode }) {
  return (
    <div className="relative">
      {/* Scrolls inside the column, so long lines never widen the page. */}
      <pre className="overflow-x-auto rounded-panel bg-code-bg p-4 text-xs leading-relaxed text-code-fg">
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
  const move = (e: KeyboardEvent, index: number) => {
    const next = e.key === "ArrowRight" ? index + 1 : e.key === "ArrowLeft" ? index - 1 : null;
    if (next === null) return;
    e.preventDefault();
    const target = tabs[(next + tabs.length) % tabs.length];
    onChange(target.id);
    document.getElementById(`tab-${target.id}`)?.focus();
  };
  return (
    <div role="tablist" className="mb-4 flex gap-1 overflow-x-auto border-b border-line">
      {tabs.map((t, i) => {
        const active = value === t.id;
        return (
          <button
            key={t.id}
            id={`tab-${t.id}`}
            role="tab"
            aria-selected={active}
            tabIndex={active ? 0 : -1}
            type="button"
            onClick={() => onChange(t.id)}
            onKeyDown={(e) => move(e, i)}
            className={`-mb-px whitespace-nowrap border-b-2 px-3 py-2 text-sm font-medium transition-colors duration-fast ${
              active ? "border-accent text-accent-text" : "border-transparent text-muted hover:text-fg"
            }`}
          >
            {t.label}
          </button>
        );
      })}
    </div>
  );
}

/** A switch for a setting that takes effect at once. The label is clickable too. */
export function Toggle({
  checked,
  onChange,
  label,
  description,
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label: string;
  description?: string;
}) {
  const id = useId();
  return (
    <div className="flex items-start gap-3">
      <button
        id={id}
        type="button"
        role="switch"
        aria-checked={checked}
        onClick={() => onChange(!checked)}
        className={`relative mt-0.5 inline-flex h-5 w-9 shrink-0 items-center rounded-full border transition-colors duration-fast ${
          checked ? "border-accent bg-accent" : "border-line-strong bg-sunken"
        }`}
      >
        <span
          className={`h-3.5 w-3.5 rounded-full bg-surface shadow-raised transition-transform duration-fast ease-out ${
            checked ? "translate-x-[1.125rem]" : "translate-x-0.5"
          }`}
        />
      </button>
      <div>
        <label htmlFor={id} className="cursor-pointer text-sm font-medium text-fg">
          {label}
        </label>
        {description && <p className="mt-0.5 text-sm text-muted">{description}</p>}
      </div>
    </div>
  );
}

/** Names an icon-only control, or adds a sentence to one. Shows on hover and on keyboard focus. */
export function Tooltip({ label, children }: { label: string; children: ReactElement }) {
  const id = useId();
  const [open, setOpen] = useState(false);
  return (
    <span
      className="relative inline-flex"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      onFocus={() => setOpen(true)}
      onBlur={() => setOpen(false)}
    >
      {cloneElement(children, { "aria-describedby": id } as Record<string, string>)}
      <span
        id={id}
        role="tooltip"
        className={`pointer-events-none absolute bottom-full left-1/2 z-40 mb-1.5 -translate-x-1/2 whitespace-nowrap rounded-control bg-fg px-2 py-1 text-xs text-canvas shadow-overlay transition-opacity duration-fast ${
          open ? "opacity-100" : "opacity-0"
        }`}
      >
        {label}
      </span>
    </span>
  );
}

/** Native <details> styled to match; zero JS and keyboard-accessible for free. */
export function Advanced({ title = "Advanced", children }: { title?: string; children: ReactNode }) {
  return (
    <details className="group rounded-panel border border-line bg-sunken/40 px-4 py-3">
      <summary className="flex cursor-pointer select-none list-none items-center gap-2 text-sm font-medium text-fg">
        <Icon icon={ChevronDown} size="sm" className="transition-transform duration-fast group-open:rotate-180" />
        {title}
      </summary>
      <div className="mt-4 space-y-4">{children}</div>
    </details>
  );
}

export function SecretReveal({ value, masked }: { value?: string | null; masked: string }) {
  const [shown, setShown] = useState(false);
  return (
    <div className="flex flex-wrap items-center gap-2">
      <code className="rounded-control bg-sunken px-2 py-1 font-mono text-xs text-fg">{shown && value ? value : masked}</code>
      {value && (
        <button type="button" onClick={() => setShown((v) => !v)} className="btn btn-ghost btn-sm">
          <Icon icon={shown ? EyeOff : Eye} size="sm" />
          {shown ? "Hide" : "Reveal"}
        </button>
      )}
      {value && <CopyButton value={value} className="btn-sm" />}
    </div>
  );
}
