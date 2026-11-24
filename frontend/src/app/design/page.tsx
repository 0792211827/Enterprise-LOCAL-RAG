"use client";
import { Database, FileText, Gauge, Layers, Lock, Plus, Search, Trash2 } from "lucide-react";
import { ReactNode, useEffect, useRef, useState } from "react";
import {
  Advanced,
  Breadcrumb,
  Callout,
  Chip,
  CodeBlock,
  CopyButton,
  Drawer,
  EmptyState,
  ErrorState,
  Icon,
  LoadingState,
  MetricCard,
  Modal,
  PageHeader,
  SecretReveal,
  Sheet,
  Skeleton,
  SkeletonText,
  StatusBadge,
  TBody,
  Table,
  Tabs,
  Td,
  Th,
  THead,
  Toggle,
  Tooltip,
  Tr,
  Wordmark,
  useToast,
} from "@/components/ui";

// The specimen: the whole look on one page so it can be approved before any real page is rebuilt.
// Colour swatches read their own value from the computed style, so this page can never drift from
// the tokens it is showing.

function toHex(css: string) {
  const [r, g, b] = (css.match(/[\d.]+/g) ?? []).slice(0, 3).map(Number);
  return `#${[r, g, b].map((c) => Math.round(c).toString(16).padStart(2, "0")).join("")}`.toUpperCase();
}

function Swatch({ token, name }: { token: string; name: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [hex, setHex] = useState("");
  useEffect(() => {
    if (ref.current) setHex(toHex(getComputedStyle(ref.current).backgroundColor));
  }, []);
  return (
    <div className="w-24">
      <div ref={ref} className="h-14 rounded-control border border-line-strong" style={{ background: `rgb(var(--${token}))` }} />
      <div className="mt-1.5 text-xs font-medium text-fg">{name}</div>
      <div className="font-mono text-xs text-subtle">{hex || "\u00a0"}</div>
    </div>
  );
}

const SCALES: { title: string; note: string; tokens: [string, string][] }[] = [
  {
    title: "Ink green",
    note: "The accent. Actions, links, focus, the selected tab.",
    tokens: [["ink-50", "50"], ["ink-100", "100"], ["ink-200", "200"], ["ink-500", "500"], ["ink-600", "600"], ["ink-700", "700"], ["ink-800", "800"], ["ink-900", "900"]],
  },
  {
    title: "Clay",
    note: "The secondary. Warnings and the cited passage. Never a button.",
    tokens: [["clay-50", "50"], ["clay-100", "100"], ["clay-500", "500"], ["clay-600", "600"], ["clay-700", "700"]],
  },
  {
    title: "Paper",
    note: "Warm neutrals. The User surface.",
    tokens: [["paper-50", "50"], ["paper-100", "100"], ["paper-200", "200"], ["paper-300", "300"], ["paper-600", "600"], ["paper-700", "700"], ["paper-900", "900"]],
  },
  {
    title: "Cool grey",
    note: "Cool neutrals. The Admin surface.",
    tokens: [["cool-50", "50"], ["cool-100", "100"], ["cool-200", "200"], ["cool-300", "300"], ["cool-600", "600"], ["cool-700", "700"], ["cool-900", "900"]],
  },
  {
    title: "Status",
    note: "State only: health, outcome. Each is paired with an icon.",
    tokens: [["ok", "ok"], ["warn", "warn"], ["danger", "danger"], ["info", "info"]],
  },
];

const TYPE_SCALE: [string, string, string][] = [
  ["text-4xl", "font-serif", "4xl"],
  ["text-3xl", "font-serif", "3xl"],
  ["text-2xl", "font-display", "2xl"],
  ["text-xl", "font-display", "xl"],
  ["text-lg", "font-sans", "lg"],
  ["text-md", "font-serif", "md · reading"],
  ["text-base", "font-sans", "base"],
  ["text-sm", "font-sans", "sm"],
  ["text-xs", "font-sans", "xs"],
];

const CONTRAST: [string, string, string][] = [
  ["text", "bg-canvas text-fg", "Body text on the page"],
  ["muted text", "bg-canvas text-muted", "Secondary text on the page"],
  ["subtle text", "bg-canvas text-subtle", "Hints and captions on the page"],
  ["text on surface", "bg-surface text-fg", "Body text on a card"],
  ["subtle on sunken", "bg-sunken text-subtle", "Hints on a recessed area"],
  ["accent text", "bg-surface text-accent-text", "Links and the selected tab"],
  ["primary button", "bg-accent text-accent-fg", "Primary button"],
  ["clay on soft", "bg-clay-soft text-clay-text", "Warning callout"],
  ["ok badge", "bg-ok-soft text-ok", "Healthy"],
  ["warn badge", "bg-warn-soft text-warn", "Degraded"],
  ["danger badge", "bg-danger-soft text-danger", "Failed"],
  ["info badge", "bg-info-soft text-info", "Processing"],
];

const SAMPLE_CODE = `curl http://localhost:8000/v1/chat/completions \\
  -H "Authorization: Bearer $KEY" \\
  -d '{"model": "hr-policies", "messages": [{"role": "user", "content": "How much leave do I get?"}]}'`;

function Block({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="py-5">
      <h4 className="mb-3 text-sm font-semibold text-fg">{title}</h4>
      {children}
    </section>
  );
}

function Components({ surface }: { surface: "user" | "admin" }) {
  const toast = useToast();
  const [tab, setTab] = useState("overview");
  const [shared, setShared] = useState(false);
  const [modal, setModal] = useState(false);
  const [drawer, setDrawer] = useState(false);
  const [sheet, setSheet] = useState(false);
  const [chips, setChips] = useState(["finance", "platform"]);

  return (
    <div className="divide-y divide-line">
      <Block title="Buttons">
        <div className="flex flex-wrap items-center gap-3">
          <button className="btn btn-primary">Primary</button>
          <button className="btn btn-secondary">Secondary</button>
          <button className="btn btn-ghost">Ghost</button>
          <button className="btn btn-danger">
            <Icon icon={Trash2} />
            Delete
          </button>
          <button className="btn btn-secondary btn-sm">Small</button>
          <button className="btn btn-primary" disabled>
            Disabled
          </button>
          <Tooltip label="Search everything">
            <button className="btn btn-secondary" aria-label="Search">
              <Icon icon={Search} />
            </button>
          </Tooltip>
        </div>
        <div className="mt-4 max-w-sm">
          <label className="label" htmlFor={`${surface}-input`}>
            Name
          </label>
          <input id={`${surface}-input`} className="input" placeholder="HR policies" />
        </div>
      </Block>

      <Block title="Status badges">
        <div className="flex flex-wrap gap-2">
          {["healthy", "processing", "queued", "degraded", "failed", "disabled"].map((s) => (
            <StatusBadge key={s} status={s} />
          ))}
        </div>
      </Block>

      <Block title="Metric card">
        <div className="grid max-w-xl grid-cols-2 gap-4">
          <MetricCard label="Documents" value="1,284" hint="3 added today" icon={FileText} />
          <MetricCard label="Knowledge Bases" value="6" icon={Database} />
        </div>
      </Block>

      <Block title="Tabs">
        <Tabs
          tabs={[
            { id: "overview", label: "Overview" },
            { id: "kbs", label: "Knowledge Bases" },
            { id: "retrieval", label: "Retrieval" },
          ]}
          value={tab}
          onChange={setTab}
        />
        <p className="text-sm text-muted">Showing the {tab} tab. Arrow keys move between tabs.</p>
      </Block>

      <Block title="Breadcrumb">
        <Breadcrumb items={[{ label: "Knowledge Bases", href: "/knowledge-bases" }, { label: "Finance policies", href: "#" }, { label: "Documents" }]} />
      </Block>

      <Block title="Table">
        <Table caption="Recent ingestion jobs">
          <THead>
            <tr>
              <Th>Document</Th>
              <Th>Stage</Th>
              <Th>Status</Th>
              <Th align="right">Chunks</Th>
            </tr>
          </THead>
          <TBody>
            <Tr>
              <Td>Leave policy 2025.pdf</Td>
              <Td>indexing</Td>
              <Td>
                <StatusBadge status="completed" />
              </Td>
              <Td align="right">42</Td>
            </Tr>
            <Tr>
              <Td>Expenses guide.docx</Td>
              <Td>parsing</Td>
              <Td>
                <StatusBadge status="failed" />
              </Td>
              <Td align="right">0</Td>
            </Tr>
          </TBody>
        </Table>
      </Block>

      <Block title="Chips">
        <div className="flex flex-wrap items-center gap-2">
          {chips.map((c) => (
            <Chip key={c} icon={Layers} tone="accent" onRemove={() => setChips((all) => all.filter((x) => x !== c))}>
              {c}
            </Chip>
          ))}
          <Chip icon={Lock} tone="clay">
            Restricted
          </Chip>
          <Chip>everyone</Chip>
        </div>
      </Block>

      <Block title="Toggle">
        <Toggle
          checked={shared}
          onChange={setShared}
          label="Share this exchange with Admins"
          description="Admins will see only this question and its answer, not the rest of the Conversation."
        />
        <div className="mt-4">
          <button className="btn btn-secondary" onClick={() => toast({ title: "Changes saved", description: "Applied to every new Conversation." })}>
            Show toast
          </button>
        </div>
      </Block>

      <Block title="Skeleton">
        <div className="max-w-sm space-y-3">
          <Skeleton className="h-5 w-1/3" />
          <SkeletonText lines={3} />
        </div>
      </Block>

      <Block title="Callouts">
        <div className="space-y-3">
          <Callout title="Grants only add access">Knowledge Base Grants combine; a Document restriction can only narrow them.</Callout>
          <Callout tone="warn" title="No one can read this Knowledge Base yet">
            It has no Grants and the platform denies ungranted access.
          </Callout>
          <Callout tone="danger">The provider did not answer at http://ollama:11434.</Callout>
          <Callout tone="ok">Connection works. 4 models found.</Callout>
        </div>
      </Block>

      <Block title="Loading, empty and error states">
        <div className="space-y-4">
          <LoadingState label="Loading Applications…" />
          <EmptyState
            icon={Gauge}
            title="No Applications yet"
            description="An Application ties Knowledge Bases to a model and a prompt."
            action={
              <button className="btn btn-primary">
                <Icon icon={Plus} />
                Create Application
              </button>
            }
          />
          <ErrorState message="The API did not answer. Check that the backend is running." onRetry={() => undefined} />
        </div>
      </Block>

      <Block title="Code block">
        <CodeBlock code={SAMPLE_CODE} action={<CopyButton value={SAMPLE_CODE} className="btn-sm" />} />
      </Block>

      <Block title="Secret reveal">
        <SecretReveal value="sk-live-specimen-0000" masked="sk-live-••••••••0000" />
      </Block>

      <Block title="Disclosure">
        <Advanced title="Advanced settings">
          <p className="text-sm text-muted">Rarely changed options live here, closed by default.</p>
        </Advanced>
      </Block>

      <Block title="Overlays">
        <div className="flex flex-wrap gap-3">
          <button className="btn btn-secondary" onClick={() => setModal(true)}>
            Open modal
          </button>
          <button className="btn btn-secondary" onClick={() => setDrawer(true)}>
            Open drawer
          </button>
          <button className="btn btn-secondary" onClick={() => setSheet(true)}>
            Open sheet
          </button>
        </div>
        <Modal open={modal} onClose={() => setModal(false)} title="Delete Application">
          <p className="text-sm text-muted">This removes “HR policies” and revokes its API keys. Knowledge Bases are kept.</p>
          <div className="mt-5 flex justify-end gap-2">
            <button className="btn btn-secondary" onClick={() => setModal(false)}>
              Cancel
            </button>
            <button className="btn btn-danger" onClick={() => setModal(false)}>
              Delete HR policies
            </button>
          </div>
        </Modal>
        <Drawer open={drawer} onClose={() => setDrawer(false)} title="Audit event">
          <dl className="space-y-3 text-sm">
            <div>
              <dt className="text-muted">Actor</dt>
              <dd className="font-mono text-xs">sam.okafor@example.test</dd>
            </div>
            <div>
              <dt className="text-muted">Action</dt>
              <dd>Application deleted</dd>
            </div>
          </dl>
        </Drawer>
        <Sheet open={sheet} onClose={() => setSheet(false)} title="Citation">
          <p className="reading">
            Full-time employees receive <mark className="rounded-sm bg-clay-soft px-0.5 text-fg">25 days of paid annual leave</mark> per calendar year.
          </p>
        </Sheet>
      </Block>
    </div>
  );
}

function Surface({ surface, title }: { surface: "user" | "admin"; title: string }) {
  return (
    <section aria-label={title} data-surface={surface} className="rounded-panel border border-line-strong bg-canvas p-6 text-fg">
      <h3 className="font-display text-xl font-semibold">{title}</h3>
      <p className="mt-1 max-w-prose text-sm text-muted">
        {surface === "user"
          ? "Calm and editorial: warm paper, serif reading text, generous space."
          : "Dense and scannable: cool grey, tight spacing, sans throughout."}
      </p>

      <div className="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-3">
        {CONTRAST.map(([name, classes, sample]) => (
          <div key={name} data-contrast={`${title}: ${name}`} className={`rounded-control border border-line px-3 py-2 text-sm ${classes}`}>
            {sample}
          </div>
        ))}
      </div>

      {surface === "user" && (
        <div className="mt-6 rounded-panel border border-line bg-surface p-6">
          <p className="reading">
            Annual leave is 25 days for full-time employees<a className="cite" href="#specimen">1</a>, and unused days carry over for one quarter<a className="cite" href="#specimen">2</a>. Part-time staff accrue leave in proportion to their contracted hours.
          </p>
        </div>
      )}

      <div className="mt-4 rounded-panel border border-line bg-surface px-5">
        <Components surface={surface} />
      </div>
    </section>
  );
}

export default function DesignSpecimenPage() {
  return (
    <main id="main" tabIndex={-1} className="space-y-12 outline-none">
      <PageHeader
        title="Design specimen"
        description="The palette, type and base components that both surfaces are built on. Nothing here is a page; it exists to be approved."
      />

      <section aria-labelledby="palette">
        <h2 id="palette" className="mb-4 font-display text-xl font-semibold">
          Palette
        </h2>
        <div className="space-y-6">
          {SCALES.map((scale) => (
            <div key={scale.title}>
              <div className="mb-2 flex flex-wrap items-baseline gap-x-3">
                <h3 className="text-sm font-semibold">{scale.title}</h3>
                <p className="text-sm text-muted">{scale.note}</p>
              </div>
              <div className="flex flex-wrap gap-3">
                {scale.tokens.map(([token, name]) => (
                  <Swatch key={token} token={token} name={name} />
                ))}
              </div>
            </div>
          ))}
        </div>
      </section>

      <section aria-labelledby="type">
        <h2 id="type" className="mb-4 font-display text-xl font-semibold">
          Type
        </h2>
        <div className="grid gap-8 lg:grid-cols-[1fr_1.2fr]">
          <div className="space-y-6">
            <div>
              <Wordmark className="text-3xl" />
              <p className="mt-2 text-sm text-muted">Newsreader, the wordmark and reading text. Headings in the User surface.</p>
            </div>
            <div>
              <p className="text-lg font-medium">Instrument Sans for every control, label and table.</p>
              <p className="mt-1 text-sm text-muted">Instrument Sans: the interface, and headings in the Admin surface.</p>
            </div>
            <div>
              <code className="text-sm">kb_7f3a91c2 · sk-live-3c9e…a41d</code>
              <p className="mt-1 text-sm text-muted">JetBrains Mono: identifiers, keys and code.</p>
            </div>
          </div>
          <div className="divide-y divide-line rounded-panel border border-line bg-surface">
            {TYPE_SCALE.map(([size, family, label]) => (
              <div key={size} className="flex items-baseline gap-4 px-4 py-2">
                <span className="w-28 shrink-0 font-mono text-xs text-subtle">{label}</span>
                <span className={`${size} ${family} truncate`}>Answers cite their sources</span>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section aria-labelledby="components">
        <h2 id="components" className="mb-4 font-display text-xl font-semibold">
          Components
        </h2>
        <div className="grid gap-8 xl:grid-cols-2">
          <Surface surface="user" title="User surface" />
          <Surface surface="admin" title="Admin surface" />
        </div>
      </section>
    </main>
  );
}
