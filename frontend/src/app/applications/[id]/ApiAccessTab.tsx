"use client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound, Plus, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";
import {
  Callout,
  CodeBlock,
  ConfirmModal,
  CopyButton,
  EmptyState,
  ErrorState,
  Icon,
  TBody,
  Table,
  TableSkeleton,
  Tabs,
  Td,
  Th,
  THead,
  Tr,
} from "@/components/ui";
import { API_BASE } from "@/lib/api/client";
import { formatDateTime } from "@/lib/format";
import { applicationsApi, openaiApi, systemApi } from "@/lib/api/resources";
import { curlSnippet, pythonSnippet, typescriptSnippet } from "@/lib/snippets";
import type { ApiKey, Application, ChatCompletionResponse } from "@/lib/api/types";

// States what the deployment actually enforces (from /system/security) rather than a fixed caveat,
// so the UI never over- or under-states the control.
function KeyEnforcement() {
  const security = useQuery({ queryKey: ["system", "security"], queryFn: systemApi.security });
  if (!security.data) return null;
  const { api_keys_enforced, auth_mode, user_token_header } = security.data;
  if (api_keys_enforced) {
    return (
      <Callout tone="info">
        Keys are <strong>enforced</strong>: requests need <code>Authorization: Bearer &lt;key&gt;</code> with an active key issued for this Application.
        {user_token_header && (
          <>
            {" "}
            To scope a request to one of your users, forward their token in <code>{user_token_header}</code>.
          </>
        )}
      </Callout>
    );
  }
  if (auth_mode !== "disabled") {
    return (
      <Callout tone="warn">
        Keys are <strong>not enforced</strong> (<code>API_KEYS__ENFORCE=false</code>). Callers need either a valid key or their own identity (
        {auth_mode === "jwt" ? "an IdP token" : "proxy-asserted headers"}).
      </Callout>
    );
  }
  return (
    <Callout tone="warn">
      Keys are issued but <strong>not enforced</strong>, and authentication is off. The endpoint accepts any request, with or without an <code>Authorization</code> header. Keep it on a
      trusted network, or set <code>API_KEYS__ENFORCE=true</code>.
    </Callout>
  );
}

export function ApiAccessTab({ app }: { app: Application }) {
  const qc = useQueryClient();
  const [baseUrl, setBaseUrl] = useState(API_BASE);
  const [sample, setSample] = useState("curl");
  const [newKey, setNewKey] = useState<string | null>(null);
  const [keyName, setKeyName] = useState("");
  const [rotating, setRotating] = useState<ApiKey | null>(null);
  const [revoking, setRevoking] = useState<ApiKey | null>(null);
  const [testResult, setTestResult] = useState<string | null>(null);
  const [testError, setTestError] = useState<string | null>(null);

  // A new Application is created with a key; the creation page hands the one-time plaintext over in memory.
  useEffect(() => {
    const w = window as unknown as { __ragNewKey?: string };
    if (w.__ragNewKey) {
      setNewKey(w.__ragNewKey);
      delete w.__ragNewKey;
    }
  }, []);

  const keys = useQuery({ queryKey: ["app-keys", app.id], queryFn: () => applicationsApi.keys(app.id) });
  const activeKeys = (keys.data ?? []).filter((k) => k.is_active);
  const refresh = () => qc.invalidateQueries({ queryKey: ["app-keys", app.id] });

  const createKey = useMutation({
    mutationFn: () => applicationsApi.createKey(app.id, keyName.trim() || undefined),
    onSuccess: (k) => {
      setNewKey(k.key);
      setKeyName("");
      refresh();
    },
  });
  const rotateKey = useMutation({
    mutationFn: (keyId: string) => applicationsApi.rotateKey(app.id, keyId),
    onSuccess: (k) => {
      setNewKey(k.key);
      setRotating(null);
      refresh();
    },
  });
  const revokeKey = useMutation({
    mutationFn: (keyId: string) => applicationsApi.revokeKey(app.id, keyId),
    onSuccess: () => {
      setRevoking(null);
      refresh();
    },
  });
  const sendTest = useMutation({
    mutationFn: (): Promise<ChatCompletionResponse> =>
      openaiApi.chatCompletion(baseUrl, app.slug, [{ role: "user", content: "Give me a one sentence summary of what you know." }], newKey ?? undefined),
    onSuccess: (data) => {
      setTestError(null);
      setTestResult(JSON.stringify(data, null, 2));
    },
    onError: (e) => {
      setTestResult(null);
      setTestError((e as Error).message);
    },
  });

  const snippets: Record<string, string> = {
    curl: curlSnippet(baseUrl, app.slug, newKey),
    python: pythonSnippet(baseUrl, app.slug, newKey),
    typescript: typescriptSnippet(baseUrl, app.slug, newKey),
  };

  return (
    <div className="space-y-8">
      {newKey && (
        <Callout tone="warn" title="Copy your new API key now. It is shown only once.">
          <p className="mt-1">It is stored hashed and cannot be shown again. If you lose it, rotate it or create another.</p>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <code data-testid="new-api-key" className="min-w-0 break-all rounded-control bg-surface px-2.5 py-1.5 font-mono text-xs text-fg ring-1 ring-line-strong">
              {newKey}
            </code>
            <CopyButton value={newKey} label="Copy key" />
            <button type="button" className="btn btn-ghost" onClick={() => setNewKey(null)}>
              I have saved it
            </button>
          </div>
        </Callout>
      )}

      <section aria-labelledby="keys-heading">
        <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
          <div>
            <h2 id="keys-heading" className="font-display text-lg font-semibold">
              API keys
            </h2>
            <p className="text-sm text-muted">Each key belongs to this Application only.</p>
          </div>
          <form
            className="flex items-end gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              createKey.mutate();
            }}
          >
            <div>
              <label className="label" htmlFor="key-name">
                Key name (optional)
              </label>
              <input id="key-name" className="input w-48" value={keyName} placeholder="Intranet search" onChange={(e) => setKeyName(e.target.value)} />
            </div>
            <button type="submit" className="btn btn-primary" disabled={createKey.isPending}>
              <Icon icon={Plus} size="sm" />
              {createKey.isPending ? "Creating…" : "Create key"}
            </button>
          </form>
        </div>

        <div className="mb-3">
          <KeyEnforcement />
        </div>
        {createKey.isError && <ErrorState title="Could not create a key" message={(createKey.error as Error).message} />}

        {keys.isLoading && <TableSkeleton columns={4} rows={2} label="Loading API keys" />}
        {keys.isError && <ErrorState title="Could not load API keys" message={(keys.error as Error).message} onRetry={() => keys.refetch()} />}
        {keys.data && activeKeys.length === 0 && (
          <EmptyState icon={KeyRound} title="No active keys" description="Create a key so software can call this Application's endpoint." />
        )}
        {activeKeys.length > 0 && (
          <Table caption="Active API keys">
            <THead>
              <tr>
                <Th>Key</Th>
                <Th>Name</Th>
                <Th>Created</Th>
                <Th>Last used</Th>
                <Th />
              </tr>
            </THead>
            <TBody>
              {activeKeys.map((k) => (
                <Tr key={k.id}>
                  <Td mono>
                    {k.key_prefix}…{k.key_last4}
                  </Td>
                  <Td>{k.name ?? "—"}</Td>
                  <Td>{formatDateTime(k.created_at)}</Td>
                  <Td>{k.last_used_at ? formatDateTime(k.last_used_at) : "Never"}</Td>
                  <Td align="right">
                    <div className="flex justify-end gap-1.5">
                      <button type="button" className="btn btn-secondary btn-sm" onClick={() => setRotating(k)} aria-label={`Rotate key ${k.key_prefix}…${k.key_last4}`}>
                        <Icon icon={RefreshCw} size="sm" />
                        Rotate
                      </button>
                      <button type="button" className="btn btn-danger btn-sm" onClick={() => setRevoking(k)} aria-label={`Revoke key ${k.key_prefix}…${k.key_last4}`}>
                        Revoke
                      </button>
                    </div>
                  </Td>
                </Tr>
              ))}
            </TBody>
          </Table>
        )}
      </section>

      <section aria-labelledby="endpoint-heading">
        <h2 id="endpoint-heading" className="font-display text-lg font-semibold">
          Endpoint
        </h2>
        <p className="mb-4 text-sm text-muted">OpenAI-compatible. Point any OpenAI SDK at this address.</p>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div>
            <label className="label" htmlFor="base-url">
              Base URL
            </label>
            <div className="flex gap-2">
              <input id="base-url" className="input font-mono text-xs" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} />
              <CopyButton value={`${baseUrl}/v1`} />
            </div>
            <p className="mt-1.5 text-xs text-muted">
              As reachable from this browser. Software on another host or container needs that network&apos;s address, for example <code>http://api:8000</code>.
            </p>
          </div>
          <div>
            <label className="label" htmlFor="model-slug">
              Model name
            </label>
            <div className="flex gap-2">
              <input id="model-slug" className="input font-mono text-xs" value={app.slug} readOnly />
              <CopyButton value={app.slug} />
            </div>
            <p className="mt-1.5 text-xs text-muted">
              Pass this as the <code>model</code> parameter.
            </p>
          </div>
        </div>

        <h3 className="mb-2 mt-6 text-sm font-semibold text-fg">Code samples</h3>
        <Tabs
          tabs={[
            { id: "curl", label: "curl" },
            { id: "python", label: "Python" },
            { id: "typescript", label: "TypeScript" },
          ]}
          value={sample}
          onChange={setSample}
        />
        <CodeBlock code={snippets[sample]} action={<CopyButton value={snippets[sample]} label="Copy sample" quiet className="!text-code-fg hover:!bg-code-fg/15" />} />
        <p className="mt-2 text-xs text-muted">
          Only the last <code>user</code> message is used; earlier messages are not carried into retrieval.
        </p>

        <div className="mt-5 flex flex-wrap items-center gap-3 border-t border-line pt-4">
          <button type="button" className="btn btn-secondary" onClick={() => sendTest.mutate()} disabled={sendTest.isPending}>
            {sendTest.isPending ? "Sending…" : "Send test request"}
          </button>
          <span className="text-xs text-muted">
            Calls <code>POST {baseUrl}/v1/chat/completions</code>, the same route as the samples.
          </span>
        </div>
        {testError && (
          <div className="mt-3">
            <ErrorState title="The test request failed" message={testError} />
          </div>
        )}
        {testResult && (
          <div className="mt-3">
            <CodeBlock code={testResult} />
          </div>
        )}
      </section>

      <ConfirmModal
        open={!!rotating}
        onClose={() => setRotating(null)}
        title="Rotate this key?"
        confirmLabel="Rotate key"
        pending={rotateKey.isPending}
        error={rotateKey.isError ? (rotateKey.error as Error).message : null}
        onConfirm={() => rotating && rotateKey.mutate(rotating.id)}
      >
        <p>
          A new key replaces <code className="font-mono">{rotating?.key_prefix}…{rotating?.key_last4}</code>. The old key stops working at once, so update whatever uses it.
        </p>
      </ConfirmModal>
      <ConfirmModal
        open={!!revoking}
        onClose={() => setRevoking(null)}
        title="Revoke this key?"
        confirmLabel="Revoke key"
        pending={revokeKey.isPending}
        error={revokeKey.isError ? (revokeKey.error as Error).message : null}
        onConfirm={() => revoking && revokeKey.mutate(revoking.id)}
      >
        <p>
          <code className="font-mono">{revoking?.key_prefix}…{revoking?.key_last4}</code> stops working at once. Clients using it will be refused.
        </p>
      </ConfirmModal>
    </div>
  );
}
