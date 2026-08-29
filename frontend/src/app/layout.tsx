import type { Metadata } from "next";
import "./globals.css";
import { Sidebar } from "@/components/Sidebar";
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
          <Sidebar />
          <main className="ml-64 min-h-screen">
            <div className="mx-auto max-w-6xl px-8 py-8">{children}</div>
          </main>
        </Providers>
      </body>
    </html>
  );
}
