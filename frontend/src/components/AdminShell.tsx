"use client";
import { MessageSquare } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { ReactNode } from "react";
import { AuthBanner, CallerBadge } from "@/components/AuthStatus";
import { Breadcrumb, Icon, Wordmark } from "@/components/ui";
import { ADMIN_NAV, NAV_GROUPS, breadcrumbFor, isActive } from "@/lib/nav";
import type { Role } from "@/lib/surfaces";

// The Admin UI: dense and scannable, cool neutrals. A sidebar grouped by the work (Build, Operate,
// Review) that shrinks to icons below the desktop breakpoint, and a bar above the page for where
// you are. Pages bring their own header and primary action.
export function AdminShell({
  roles,
  onSignIn,
  onSignOut,
  children,
}: {
  roles: Role[];
  onSignIn?: () => void;
  onSignOut?: () => void;
  children: ReactNode;
}) {
  const pathname = usePathname();
  const visible = ADMIN_NAV.filter((item) => roles.includes(item.role));
  const crumbs = breadcrumbFor(pathname);

  return (
    <div data-surface="admin" className="min-h-screen bg-canvas text-fg">
      <aside className="fixed inset-y-0 left-0 z-30 flex w-14 flex-col border-r border-line bg-surface lg:w-60">
        <Link href="/" className="flex h-14 shrink-0 items-center border-b border-line px-3.5 lg:px-5" aria-label="Enterprise Local RAG home">
          <span className="flex h-7 w-7 items-center justify-center rounded-control bg-accent font-serif text-sm font-semibold text-accent-fg lg:hidden">E</span>
          <span className="hidden lg:block">
            <Wordmark className="text-base" />
          </span>
        </Link>

        <nav aria-label="Main" className="flex-1 overflow-y-auto px-2 py-3 lg:px-3">
          {NAV_GROUPS.map((group, i) => {
            const items = visible.filter((item) => item.group === group);
            if (items.length === 0) return null;
            return (
              <div key={group} className={i > 0 ? "mt-4 border-t border-line pt-4 lg:border-t-0 lg:pt-0" : ""}>
                <div className="mb-1 hidden px-2 text-xs font-medium text-subtle lg:block">{group}</div>
                <ul className="space-y-0.5">
                  {items.map((item) => {
                    const active = isActive(item, pathname);
                    return (
                      <li key={item.href}>
                        <Link
                          href={item.href}
                          aria-label={item.label}
                          aria-current={active ? "page" : undefined}
                          title={item.label}
                          className={`flex items-center justify-center gap-3 rounded-control px-2.5 py-2 text-sm font-medium transition-colors duration-fast lg:justify-start ${
                            active ? "bg-accent-soft text-accent-text" : "text-muted hover:bg-sunken hover:text-fg"
                          }`}
                        >
                          <Icon icon={item.icon} size="lg" />
                          <span className="hidden lg:inline">{item.label}</span>
                        </Link>
                      </li>
                    );
                  })}
                </ul>
              </div>
            );
          })}
        </nav>

        <div className="shrink-0 space-y-3 border-t border-line p-2 lg:p-4">
          {roles.includes("querier") && (
            <Link
              href="/chat"
              aria-label="Open chat"
              title="Open chat"
              className="flex items-center justify-center gap-3 rounded-control px-2.5 py-2 text-sm text-muted hover:bg-sunken hover:text-fg lg:justify-start"
            >
              <Icon icon={MessageSquare} size="lg" />
              <span className="hidden lg:inline">Open chat</span>
            </Link>
          )}
          <CallerBadge onSignOut={onSignOut} collapsible />
        </div>
      </aside>

      <div className="ml-14 lg:ml-60">
        <header className="flex h-14 items-center border-b border-line bg-surface px-6 lg:px-8">
          {crumbs.length > 0 && <Breadcrumb items={crumbs} />}
        </header>
        <main id="main" tabIndex={-1} className="mx-auto max-w-6xl px-6 py-6 outline-none lg:px-8">
          <AuthBanner onSignIn={onSignIn} />
          {children}
        </main>
      </div>
    </div>
  );
}
