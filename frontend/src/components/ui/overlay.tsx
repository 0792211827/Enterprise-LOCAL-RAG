"use client";
import { X } from "lucide-react";
import { ReactNode, useEffect, useId, useRef } from "react";
import { Icon } from "./Icon";

type Placement = "center" | "right" | "bottom";

const FOCUSABLE = 'a[href], button:not([disabled]), textarea, input:not([disabled]), select, [tabindex]:not([tabindex="-1"])';

const PLACEMENT: Record<Placement, { wrap: string; panel: string }> = {
  center: {
    wrap: "items-center justify-center p-4",
    panel: "w-full rounded-panel animate-rise-in",
  },
  right: {
    wrap: "items-stretch justify-end",
    panel: "h-full w-full rounded-l-panel animate-slide-in-right",
  },
  bottom: {
    wrap: "items-end justify-center",
    panel: "max-h-[85vh] w-full rounded-t-panel animate-slide-in-bottom",
  },
};

const WIDTHS: Record<string, string> = {
  md: "max-w-lg",
  lg: "max-w-2xl",
  xl: "max-w-4xl",
};

/**
 * Shared behaviour of Modal, Drawer and Sheet: a labelled dialog that traps Tab, closes on Escape or a
 * click on the backdrop, locks page scroll, and hands focus back to whatever opened it.
 */
function Overlay({
  open,
  onClose,
  title,
  children,
  placement,
  width,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  placement: Placement;
  width: string;
}) {
  const titleId = useId();
  const panel = useRef<HTMLDivElement>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    if (!open) return;
    const opener = document.activeElement as HTMLElement | null;
    const scrollLock = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    panel.current?.focus();

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onCloseRef.current();
        return;
      }
      if (e.key !== "Tab" || !panel.current) return;
      const items = Array.from(panel.current.querySelectorAll<HTMLElement>(FOCUSABLE));
      if (items.length === 0) {
        e.preventDefault();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      const active = document.activeElement;
      if (e.shiftKey && (active === first || active === panel.current)) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && active === last) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = scrollLock;
      opener?.focus?.();
    };
  }, [open]);

  if (!open) return null;
  const { wrap, panel: panelClass } = PLACEMENT[placement];
  return (
    <div
      className={`fixed inset-0 z-50 flex animate-fade-in bg-overlay/45 ${wrap}`}
      // Only a click on the backdrop itself closes; a drag that ends here
      // shouldn't discard whatever the person was typing.
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className={`flex flex-col border border-line bg-surface text-fg shadow-overlay outline-none ${panelClass} ${width}`}
      >
        <div className="flex items-center justify-between gap-4 border-b border-line px-6 py-4">
          <h2 id={titleId} className="font-display text-lg font-semibold">
            {title}
          </h2>
          <button type="button" onClick={onClose} className="btn btn-ghost btn-sm -mr-2" aria-label="Close">
            <Icon icon={X} />
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">{children}</div>
      </div>
    </div>
  );
}

/** A decision or short form that needs the person's full attention. */
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
  return (
    <Overlay open={open} onClose={onClose} title={title} placement="center" width={WIDTHS[size]}>
      {children}
    </Overlay>
  );
}

/** Detail beside the page it came from, such as one audit event. */
export function Drawer({
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
  size?: "md" | "lg";
}) {
  return (
    <Overlay open={open} onClose={onClose} title={title} placement="right" width={size === "lg" ? "max-w-2xl" : "max-w-md"}>
      {children}
    </Overlay>
  );
}

/** The narrow-screen form of a side panel: rises from the bottom edge. */
export function Sheet({
  open,
  onClose,
  title,
  children,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
}) {
  return (
    <Overlay open={open} onClose={onClose} title={title} placement="bottom" width="max-w-none">
      {children}
    </Overlay>
  );
}

export function confirmDialog(message: string): boolean {
  if (typeof window === "undefined") return false;
  return window.confirm(message);
}
