"use client";
import { ChevronRight } from "lucide-react";
import Link from "next/link";
import { ReactNode } from "react";
import { Icon } from "./Icon";

export interface BreadcrumbItem {
  label: string;
  href?: string;
}

export function Breadcrumb({ items }: { items: BreadcrumbItem[] }) {
  return (
    <nav aria-label="Breadcrumb">
      <ol className="flex flex-wrap items-center gap-1 text-sm text-muted">
        {items.map((item, i) => {
          const last = i === items.length - 1;
          return (
            <li key={`${item.label}-${i}`} className="flex items-center gap-1">
              {item.href && !last ? (
                <Link href={item.href} className="rounded-sm hover:text-fg hover:underline">
                  {item.label}
                </Link>
              ) : (
                <span aria-current={last ? "page" : undefined} className={last ? "text-fg" : undefined}>
                  {item.label}
                </span>
              )}
              {!last && <Icon icon={ChevronRight} size="sm" className="text-subtle" />}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

export function PageHeader({
  title,
  description,
  action,
  breadcrumb,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
  breadcrumb?: BreadcrumbItem[];
}) {
  return (
    <header className="mb-6">
      {breadcrumb && breadcrumb.length > 0 && (
        <div className="mb-2">
          <Breadcrumb items={breadcrumb} />
        </div>
      )}
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="font-display text-2xl font-semibold text-fg">{title}</h1>
          {description && <p className="mt-1 max-w-prose text-sm text-muted">{description}</p>}
        </div>
        {action}
      </div>
    </header>
  );
}
