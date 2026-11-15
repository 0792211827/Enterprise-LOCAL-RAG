"use client";
import { Plug } from "lucide-react";
import { EmptyState } from "@/components/ui";

/** Held open for the Connectors ticket. */
export function ConnectorsTab() {
  return (
    <EmptyState
      icon={Plug}
      title="Connectors are coming to this tab"
      description="Placeholder. Here you will connect a mounted folder or an S3-compatible bucket to keep this Knowledge Base in sync without manual upload."
    />
  );
}
