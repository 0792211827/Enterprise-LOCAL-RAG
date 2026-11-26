import {
  AppWindow,
  Cpu,
  FileText,
  LayoutDashboard,
  Library,
  ScrollText,
  Server,
  type LucideIcon,
} from "lucide-react";
import type { Role } from "./surfaces";

// The Admin UI's navigation, grouped by the work being done: Build the things people ask about,
// Operate the platform, Review what happened. An entry shows only for callers holding its role.

export type NavGroup = "Build" | "Operate" | "Review";

export interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
  group: NavGroup;
  role: Role;
}

export const NAV_GROUPS: NavGroup[] = ["Build", "Operate", "Review"];

export const ADMIN_NAV: NavItem[] = [
  { href: "/applications", label: "Applications", icon: AppWindow, group: "Build", role: "admin" },
  { href: "/knowledge-bases", label: "Knowledge Bases", icon: Library, group: "Build", role: "admin" },
  { href: "/documents", label: "Documents", icon: FileText, group: "Build", role: "admin" },
  { href: "/", label: "Dashboard", icon: LayoutDashboard, group: "Operate", role: "admin" },
  { href: "/providers", label: "Models & Providers", icon: Cpu, group: "Operate", role: "admin" },
  { href: "/system", label: "System", icon: Server, group: "Operate", role: "admin" },
  { href: "/audit", label: "Audit Log", icon: ScrollText, group: "Review", role: "auditor" },
];

export function isActive(item: NavItem, pathname: string) {
  return item.href === "/" ? pathname === "/" : pathname === item.href || pathname.startsWith(`${item.href}/`);
}

/** The breadcrumb for a path: its group, its page, then what is below it ("New", "Details"). */
export function breadcrumbFor(pathname: string): { label: string; href?: string }[] {
  const item = ADMIN_NAV.find((i) => isActive(i, pathname));
  if (!item) return [];
  const crumbs: { label: string; href?: string }[] = [{ label: item.group }];
  const rest = pathname.slice(item.href === "/" ? 1 : item.href.length).split("/").filter(Boolean);
  if (rest.length === 0) return [...crumbs, { label: item.label }];
  crumbs.push({ label: item.label, href: item.href });
  crumbs.push({ label: rest[0] === "new" ? "New" : "Details" });
  return crumbs;
}
