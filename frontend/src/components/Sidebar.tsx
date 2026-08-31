"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";

// Assistants first: it is the object admins actually come here for. The
// infrastructure pages are grouped under a Settings heading below.
const NAV = [
  { href: "/applications", label: "Assistants", icon: "◈" },
  { href: "/", label: "Dashboard", icon: "▦" },
  { href: "/knowledge-bases", label: "Knowledge Bases", icon: "▤" },
  { href: "/documents", label: "Documents", icon: "▧" },
  { href: "/providers", label: "Models & Providers", icon: "◍", section: "Settings" },
  { href: "/retrieval", label: "Retrieval", icon: "⇄" },
  { href: "/system", label: "System", icon: "❤" },
];

export function Sidebar() {
  const pathname = usePathname();
  return (
    <aside className="fixed inset-y-0 left-0 w-64 border-r border-slate-200 bg-white">
      <div className="flex h-16 items-center gap-2 border-b border-slate-200 px-5">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand-600 text-sm font-bold text-white">
          R
        </div>
        <div>
          <div className="text-sm font-semibold leading-tight">Enterprise Local RAG</div>
          <div className="text-xs text-slate-400">Admin Control Plane</div>
        </div>
      </div>
      <nav className="p-3">
        {NAV.map((item) => {
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
    </aside>
  );
}
