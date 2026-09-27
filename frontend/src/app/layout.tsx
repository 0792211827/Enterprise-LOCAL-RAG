import type { Metadata } from "next";
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
