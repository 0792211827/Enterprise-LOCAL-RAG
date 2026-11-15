import type { Metadata } from "next";
// Fonts are bundled with the app (no CDN): serif for the wordmark and reading text, sans for the
// interface, mono for identifiers and code.
import "@fontsource-variable/newsreader/wght.css";
import "@fontsource-variable/newsreader/wght-italic.css";
import "@fontsource-variable/instrument-sans";
import "@fontsource-variable/jetbrains-mono";
import "./globals.css";
import { AppShell } from "./AppShell";
import { Providers } from "./providers";

export const metadata: Metadata = {
  title: "Enterprise Local RAG — Admin Control Plane",
  description: "Self-hosted Enterprise Local RAG control plane",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <Providers>
          <AppShell>{children}</AppShell>
        </Providers>
      </body>
    </html>
  );
}
