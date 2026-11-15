"use client";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Callout, Modal, Toggle } from "@/components/ui";
import { connectorsApi } from "@/lib/api/resources";
import type { Connector, ConnectorCreate, ConnectorSourceType } from "@/lib/api/types";
import { CONNECTOR_NAME, ENV_REFERENCE } from "./connectors";

interface Form {
  name: string;
  source_type: ConnectorSourceType;
  enabled: boolean;
  path: string;
  endpoint_url: string;
  bucket: string;
  prefix: string;
  region: string;
  access_key_ref: string;
  secret_key_ref: string;
}

const fromConnector = (c: Connector | null): Form => ({
  name: c?.name ?? "",
  source_type: c?.source_type ?? "path",
  enabled: c?.enabled ?? true,
  path: c?.source.path ?? "",
  endpoint_url: c?.source.endpoint_url ?? "",
  bucket: c?.source.bucket ?? "",
  prefix: c?.source.prefix ?? "",
  region: c?.source.region ?? "",
  access_key_ref: c?.source.access_key_ref ?? "",
  secret_key_ref: c?.source.secret_key_ref ?? "",
});

const SOURCES: { value: ConnectorSourceType; label: string; hint: string }[] = [
  { value: "path", label: "Mounted folder", hint: "A shared folder mounted where the Connector job runs." },
  { value: "s3", label: "S3-compatible bucket", hint: "AWS S3, MinIO or any service that speaks S3." },
];

function CredentialField({ id, label, value, onChange }: { id: string; label: string; value: string; onChange: (v: string) => void }) {
  const invalid = value !== "" && !ENV_REFERENCE.test(value);
  return (
    <div>
      <label className="label" htmlFor={id}>
        {label}
      </label>
      <input
        id={id}
        className="input font-mono"
        value={value}
        autoComplete="off"
        spellCheck={false}
        aria-invalid={invalid}
        aria-describedby={`${id}-help`}
        placeholder="env:MINIO_ACCESS_KEY"
        onChange={(e) => onChange(e.target.value.trim())}
      />
      <p id={`${id}-help`} className={`mt-1.5 text-xs ${invalid ? "text-danger" : "text-muted"}`}>
        {invalid ? "This is not an environment-variable reference. Use the form env:NAME. Never paste the key itself." : "The name of an environment variable on the server that runs the job, as env:NAME."}
      </p>
    </div>
  );
}

/** Create a Connector for this Knowledge Base, or edit one. Credentials are `env:NAME` references only. */
export function ConnectorForm({ kbId, connector, onClose }: { kbId: string; connector: Connector | null; onClose: () => void }) {
  const qc = useQueryClient();
  const editing = connector !== null;
  const [form, setForm] = useState<Form>(() => fromConnector(connector));
  const patch = (p: Partial<Form>) => setForm((f) => ({ ...f, ...p }));
  const s3 = form.source_type === "s3";

  const nameInvalid = form.name !== "" && !CONNECTOR_NAME.test(form.name);
  const refsInvalid = s3 && [form.access_key_ref, form.secret_key_ref].some((r) => r !== "" && !ENV_REFERENCE.test(r));
  const complete = s3
    ? [form.endpoint_url, form.bucket, form.access_key_ref, form.secret_key_ref].every((v) => v.trim() !== "")
    : form.path.trim() !== "";
  const valid = (editing || (form.name !== "" && !nameInvalid)) && complete && !refsInvalid;

  const save = useMutation({
    mutationFn: () => {
      const source = s3
        ? {
            endpoint_url: form.endpoint_url.trim(),
            bucket: form.bucket.trim(),
            prefix: form.prefix.trim(),
            region: form.region.trim(),
            access_key_ref: form.access_key_ref,
            secret_key_ref: form.secret_key_ref,
          }
        : { path: form.path.trim() };
      if (connector) return connectorsApi.update(connector.id, { enabled: form.enabled, ...source });
      const payload: ConnectorCreate = { name: form.name, knowledge_base_id: kbId, source_type: form.source_type, enabled: form.enabled, ...source };
      // An empty optional field is left out rather than sent as an empty string.
      for (const key of ["prefix", "region"] as const) if (!payload[key]) delete payload[key];
      return connectorsApi.create(payload);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["connectors"] });
      onClose();
    },
  });

  return (
    <Modal open onClose={onClose} title={editing ? `Edit ${connector.name}` : "New Connector"} size="lg">
      <form
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          if (valid) save.mutate();
        }}
      >
        {!editing && (
          <>
            <div>
              <label className="label" htmlFor="connector-name">
                Name
              </label>
              <input id="connector-name" className="input font-mono" autoFocus value={form.name} aria-invalid={nameInvalid} onChange={(e) => patch({ name: e.target.value })} placeholder="hr-share" />
              <p className={`mt-1.5 text-xs ${nameInvalid ? "text-danger" : "text-muted"}`}>Letters, numbers, dots, dashes and underscores, with no spaces. It is typed on the command line.</p>
            </div>
            <fieldset>
              <legend className="label">Source</legend>
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                {SOURCES.map((o) => (
                  <label key={o.value} className={`flex cursor-pointer items-start gap-2.5 rounded-control border p-3 text-sm ${form.source_type === o.value ? "border-accent bg-accent-soft" : "border-line-strong"}`}>
                    <input type="radio" name="source" className="mt-0.5" checked={form.source_type === o.value} onChange={() => patch({ source_type: o.value })} />
                    <span>
                      <span className="block font-medium text-fg">{o.label}</span>
                      <span className="block text-xs text-muted">{o.hint}</span>
                    </span>
                  </label>
                ))}
              </div>
            </fieldset>
          </>
        )}

        {!s3 && (
          <div>
            <label className="label" htmlFor="connector-path">
              Folder path
            </label>
            <input id="connector-path" className="input font-mono" value={form.path} onChange={(e) => patch({ path: e.target.value })} placeholder="/mnt/sources/hr" />
            <p className="mt-1.5 text-xs text-muted">Must be inside the directory the operator allows for mounted sources. Links are never followed.</p>
          </div>
        )}

        {s3 && (
          <>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <div>
                <label className="label" htmlFor="connector-endpoint">
                  Endpoint URL
                </label>
                <input id="connector-endpoint" className="input font-mono" value={form.endpoint_url} onChange={(e) => patch({ endpoint_url: e.target.value })} placeholder="https://minio.internal:9000" />
              </div>
              <div>
                <label className="label" htmlFor="connector-bucket">
                  Bucket
                </label>
                <input id="connector-bucket" className="input font-mono" value={form.bucket} onChange={(e) => patch({ bucket: e.target.value })} placeholder="documents" />
              </div>
              <div>
                <label className="label" htmlFor="connector-prefix">
                  Prefix (optional)
                </label>
                <input id="connector-prefix" className="input font-mono" value={form.prefix} onChange={(e) => patch({ prefix: e.target.value })} placeholder="hr/" />
              </div>
              <div>
                <label className="label" htmlFor="connector-region">
                  Region (optional)
                </label>
                <input id="connector-region" className="input font-mono" value={form.region} onChange={(e) => patch({ region: e.target.value })} placeholder="us-east-1" />
              </div>
            </div>
            <Callout tone="info" title="Credentials are references, not secrets">
              Put the keys in the environment of the server that runs the Connector job, and enter only the variable names here. Nothing secret is stored in the database.
            </Callout>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <CredentialField id="connector-access-key" label="Access key reference" value={form.access_key_ref} onChange={(v) => patch({ access_key_ref: v })} />
              <CredentialField id="connector-secret-key" label="Secret key reference" value={form.secret_key_ref} onChange={(v) => patch({ secret_key_ref: v })} />
            </div>
          </>
        )}

        <Toggle checked={form.enabled} onChange={(enabled) => patch({ enabled })} label="Enabled" description="A paused Connector is skipped whenever the Connector job runs." />

        {save.isError && (
          <p role="alert" className="text-sm text-danger">
            {(save.error as Error).message}
          </p>
        )}
        <div className="flex justify-end gap-2 pt-2">
          <button type="button" className="btn btn-secondary" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn btn-primary" disabled={!valid || save.isPending}>
            {save.isPending ? "Saving…" : editing ? "Save changes" : "Create Connector"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
