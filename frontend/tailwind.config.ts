import type { Config } from "tailwindcss";

// Everything here points at a CSS variable in src/styles/tokens.css. Change a token there, never a
// value here. `rgb(var(--x) / <alpha-value>)` lets utilities such as bg-accent/10 work.
const channel = (name: string) => `rgb(var(--${name}) / <alpha-value>)`;
const scale = (name: string, steps: number[]) => Object.fromEntries(steps.map((s) => [s, channel(`${name}-${s}`)]));

const SPACING_STEPS = [0.5, 1, 1.5, 2, 2.5, 3, 3.5, 4, 5, 6, 7, 8, 9, 10, 11, 12, 14, 16, 20, 24, 28, 32, 36, 40, 44, 48, 52, 56, 60, 64, 72, 80, 96];
const spacing = {
  px: "1px",
  0: "0px",
  ...Object.fromEntries(SPACING_STEPS.map((n) => [n, `calc(var(--space-unit) * ${n})`])),
};

const size = (name: string) => [`var(--text-${name})`, { lineHeight: `var(--text-${name}-lh)` }] as [string, { lineHeight: string }];

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    spacing,
    // Replaces Tailwind's default palette on purpose: only these token-backed colours exist, so a
    // generic blue or slate cannot creep back in.
    colors: {
      transparent: "transparent",
      current: "currentColor",
      canvas: channel("canvas"),
      surface: channel("surface"),
      sunken: channel("sunken"),
      line: { DEFAULT: channel("line"), strong: channel("line-strong") },
      fg: channel("fg"),
      muted: channel("muted"),
      subtle: channel("subtle"),
      accent: {
        DEFAULT: channel("accent"),
        hover: channel("accent-hover"),
        fg: channel("accent-fg"),
        soft: channel("accent-soft"),
        text: channel("accent-text"),
      },
      clay: { DEFAULT: channel("clay"), soft: channel("clay-soft"), text: channel("clay-text") },
      ok: { DEFAULT: channel("ok"), soft: channel("ok-soft") },
      warn: { DEFAULT: channel("warn"), soft: channel("warn-soft") },
      danger: { DEFAULT: channel("danger"), soft: channel("danger-soft") },
      info: { DEFAULT: channel("info"), soft: channel("info-soft") },
      overlay: channel("overlay"),
      code: { bg: channel("code-bg"), fg: channel("code-fg") },
      ring: channel("ring"),
      // Raw palette scales, for the specimen page only.
      ink: scale("ink", [50, 100, 200, 500, 600, 700, 800, 900]),
      paper: scale("paper", [50, 100, 200, 300, 600, 700, 900]),
      cool: scale("cool", [50, 100, 200, 300, 600, 700, 900]),
      "clay-scale": scale("clay", [50, 100, 500, 600, 700]),
    },
    extend: {
      fontFamily: {
        sans: "var(--font-sans)",
        serif: "var(--font-serif)",
        mono: "var(--font-mono)",
        display: "var(--font-display)",
      },
      fontSize: {
        xs: size("xs"),
        sm: size("sm"),
        base: size("base"),
        md: size("md"),
        lg: size("lg"),
        xl: size("xl"),
        "2xl": size("2xl"),
        "3xl": size("3xl"),
        "4xl": size("4xl"),
      },
      borderRadius: {
        control: "var(--radius-control)",
        panel: "var(--radius-panel)",
      },
      boxShadow: {
        raised: "var(--shadow-raised)",
        overlay: "var(--shadow-overlay)",
      },
      transitionDuration: { fast: "var(--duration-fast)", base: "var(--duration-base)", slow: "var(--duration-slow)" },
      transitionTimingFunction: { out: "var(--ease-out)" },
      keyframes: {
        shimmer: { "0%": { backgroundPosition: "200% 0" }, "100%": { backgroundPosition: "-200% 0" } },
        "fade-in": { from: { opacity: "0" }, to: { opacity: "1" } },
        "slide-in-right": { from: { transform: "translateX(100%)" }, to: { transform: "translateX(0)" } },
        "slide-in-left": { from: { transform: "translateX(-100%)" }, to: { transform: "translateX(0)" } },
        "slide-in-bottom": { from: { transform: "translateY(100%)" }, to: { transform: "translateY(0)" } },
        "rise-in": { from: { opacity: "0", transform: "translateY(6px)" }, to: { opacity: "1", transform: "translateY(0)" } },
      },
      animation: {
        shimmer: "shimmer 1.8s linear infinite",
        "fade-in": "fade-in var(--duration-base) var(--ease-out)",
        "slide-in-right": "slide-in-right var(--duration-slow) var(--ease-out)",
        "slide-in-left": "slide-in-left var(--duration-slow) var(--ease-out)",
        "slide-in-bottom": "slide-in-bottom var(--duration-slow) var(--ease-out)",
        "rise-in": "rise-in var(--duration-base) var(--ease-out)",
      },
    },
  },
  plugins: [],
};
export default config;
