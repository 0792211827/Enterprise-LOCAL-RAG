"use client";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { authApi } from "@/lib/api/resources";
import type { Role } from "@/lib/surfaces";
import { CallerBadge } from "./AuthStatus";

// Assistants first: it is the object admins actually come here for. The
// infrastructure pages are grouped under a Settings heading below.
type NavItem = { href: string; label: string; icon: string; section?: string; role: Role };

const NAV: NavItem[] = [
  { href: "/chat", label: "Chat", icon: "✎", role: "querier" },
  { href: "/applications", label: "Assistants", icon: "◈", role: "admin", section: "Admin" },
  { href: "/", label: "Dashboard", icon: "▦", role: "admin" },
  { href: "/knowledge-bases", label: "Knowledge Bases", icon: "▤", role: "admin" },
  { href: "/documents", label: "Documents", icon: "▧", role: "admin" },
  { href: "/feedback", label: "Chat feedback", icon: "☺", role: "admin" },
  { href: "/providers", label: "Models & Providers", icon: "◍", section: "Settings", role: "admin" },
  { href: "/retrieval", label: "Retrieval", icon: "⇄", role: "admin" },
  { href: "/system", label: "System", icon: "❤", role: "admin" },
  { href: "/audit", label: "Audit Log", icon: "☰", section: "Compliance", role: "auditor" },
];

export function Sidebar({ onSignOut }: { onSignOut?: () => void }) {
  const pathname = usePathname();
  // Same query (and cache entry) as the caller badge. With auth disabled the
  // anonymous caller holds every role, so everything shows as before.
  const me = useQuery({ queryKey: ["auth", "me"], queryFn: authApi.me, retry: false });
  const roles = me.data?.roles;
  // While the caller is unknown nothing is shown (so an employee never glimpses admin pages). If /auth/me
  // fails -- not signed in -- the admin pages stay listed, as before, beside the sign-in banner.
  const nav = NAV.filter((item) => (roles ? roles.includes(item.role) : me.isError && item.role === "admin"));
  // Employees get a chat app, not a control plane.
  const subtitle = !roles || roles.includes("admin") ? "Admin Control Plane" : "Company assistants";
  return (
    <aside className="fixed inset-y-0 left-0 w-64 border-r border-slate-200 bg-white">
      <div className="flex h-16 items-center gap-2 border-b border-slate-200 px-5">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand-600 text-sm font-bold text-white">
          R
        </div>
        <div>
          <div className="text-sm font-semibold leading-tight">Enterprise Local RAG</div>
          <div className="text-xs text-slate-400">{subtitle}</div>
        </div>
      </div>
      <nav className="p-3">
        {nav.map((item) => {
          const active =
            item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
          const link = (
            <Link
              key={item.href}
              href={item.href}
              className={`mb-1 flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition ${
                active
                  ? "bg-brand-50 text-brand-700"
                  : "text-slate-600 hover:bg-slate-50"
              }`}
            >
              <span className="w-4 text-center opacity-70">{item.icon}</span>
              {item.label}
            </Link>
          );
          if (!item.section) return link;
          return (
            <div key={item.href}>
              <div className="mb-1 mt-4 px-3 text-[10px] font-semibold uppercase tracking-wide text-slate-400">
                {item.section}
              </div>
              {link}
            </div>
          );
        })}
      </nav>
      <div className="absolute inset-x-0 bottom-0 border-t border-slate-200 p-4">
        <CallerBadge onSignOut={onSignOut} />
      </div>
    </aside>
  );
}
