"use client";
import { Check, ChevronDown } from "lucide-react";
import { KeyboardEvent, useEffect, useId, useRef, useState } from "react";
import { Icon } from "@/components/ui";
import type { ChatApplication } from "@/lib/api/types";

/**
 * Which Application is being asked. It shows the Application's own name and description rather than a
 * generic label, and offers the others when there are others.
 */
export function ApplicationSwitcher({
  applications,
  selected,
  onSelect,
}: {
  applications: ChatApplication[];
  selected: ChatApplication | undefined;
  onSelect: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const listId = useId();

  useEffect(() => {
    if (!open) return;
    const first = list.current?.querySelector<HTMLElement>('[aria-selected="true"]') ?? list.current?.querySelector<HTMLElement>('[role="option"]');
    first?.focus();
    const away = (e: MouseEvent) => {
      if (!list.current?.contains(e.target as Node) && !trigger.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, [open]);

  const name = selected?.name ?? "Choose an application";
  const title = (
    <h1 className="font-display text-lg font-semibold leading-tight text-fg [overflow-wrap:anywhere]">{name}</h1>
  );

  if (applications.length <= 1) {
    return (
      <div className="min-w-0">
        {title}
        {selected?.description && <p className="truncate text-sm text-muted">{selected.description}</p>}
      </div>
    );
  }

  const close = () => {
    setOpen(false);
    trigger.current?.focus();
  };
  const onKeyDown = (e: KeyboardEvent) => {
    const options = Array.from(list.current?.querySelectorAll<HTMLElement>('[role="option"]') ?? []);
    const at = options.indexOf(document.activeElement as HTMLElement);
    if (e.key === "Escape") {
      e.preventDefault();
      close();
    } else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const next = e.key === "ArrowDown" ? at + 1 : at - 1;
      options[(next + options.length) % options.length]?.focus();
    }
  };

  return (
    <div className="relative min-w-0" onKeyDown={onKeyDown}>
      <h1 className="min-w-0 font-display text-lg font-semibold leading-tight text-fg">
        <button
          ref={trigger}
          type="button"
          aria-haspopup="listbox"
          aria-expanded={open}
          aria-controls={open ? listId : undefined}
          onClick={() => setOpen((v) => !v)}
          className="-ml-2 inline-flex max-w-full items-center gap-1.5 rounded-control px-2 py-0.5 text-left hover:bg-sunken"
        >
          <span className="truncate">{name}</span>
          <Icon icon={ChevronDown} size="sm" className="text-subtle" />
        </button>
      </h1>
      {selected?.description && <p className="truncate text-sm text-muted">{selected.description}</p>}
      {open && (
        <div
          ref={list}
          id={listId}
          role="listbox"
          aria-label="Application"
          className="absolute left-0 top-full z-30 mt-2 w-[min(24rem,calc(100vw-2rem))] animate-rise-in overflow-hidden rounded-panel border border-line bg-surface p-1.5 shadow-overlay"
        >
          {applications.map((a) => (
            <button
              key={a.id}
              type="button"
              role="option"
              aria-selected={a.id === selected?.id}
              onClick={() => {
                onSelect(a.id);
                close();
              }}
              className="flex w-full items-start gap-3 rounded-control px-3 py-2.5 text-left hover:bg-sunken focus-visible:bg-sunken"
            >
              <span className="min-w-0 flex-1">
                <span className="block font-medium text-fg">{a.name}</span>
                {a.description && <span className="mt-0.5 block text-sm text-muted">{a.description}</span>}
              </span>
              {a.id === selected?.id && <Icon icon={Check} className="mt-0.5 text-accent-text" />}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
