"use client";
import { QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { ToastProvider } from "@/components/ui";
import { ApiError } from "@/lib/api/client";
import { notifyApiReachability } from "@/lib/auth";

export function Providers({ children }: { children: React.ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        queryCache: new QueryCache({
          // A failure that is not an ApiError means the request never got an answer.
          onError: (error) => !(error instanceof ApiError) && notifyApiReachability(false),
          onSuccess: () => notifyApiReachability(true),
        }),
        defaultOptions: {
          queries: { retry: 1, refetchOnWindowFocus: false, staleTime: 10_000 },
        },
      }),
  );
  return (
    <QueryClientProvider client={client}>
      <ToastProvider>{children}</ToastProvider>
    </QueryClientProvider>
  );
}
