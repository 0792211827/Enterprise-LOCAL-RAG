"use client";
import type { LucideIcon } from "lucide-react";

const SIZES = { sm: 14, md: 16, lg: 20, xl: 24 } as const;

export type IconComponent = LucideIcon;

/**
 * The one way to draw an icon. Icons are decorative unless given a `label`, so a button's text, not
 * its glyph, is what a screen reader hears.
 */
export function Icon({
  icon: Glyph,
  size = "md",
  label,
  className = "",
}: {
  icon: IconComponent;
  size?: keyof typeof SIZES;
  label?: string;
  className?: string;
}) {
  return (
    <Glyph
      size={SIZES[size]}
      strokeWidth={1.75}
      className={`shrink-0 ${className}`}
      aria-hidden={label ? undefined : true}
      aria-label={label}
      role={label ? "img" : undefined}
    />
  );
}
