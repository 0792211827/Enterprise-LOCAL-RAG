import { expect, test, type Page } from "@playwright/test";
import { document as doc, grant, job, kb, security } from "./support/admin-fixtures";
import { stubApi, type ApiStub } from "./support/api-stub";
import { captureAll } from "./support/screenshots";

const LEAVE = doc();
const SALARY = doc({ id: "doc-salary", title: "Salary bands.pdf" });

async function openAccess(page: Page, path: string, setup?: (api: ApiStub) => void) {
  const api = await stubApi(page, "admin");
  api.respond("GET", "/api/v1/knowledge-bases/:id", kb());
  api.respond("GET", "/api/v1/knowledge-bases/:id/documents", [LEAVE, SALARY]);
  api.respond("GET", "/api/v1/knowledge-bases/:id/document-restrictions", { "doc-salary": [{ principal_type: "group", principal_value: "hr-leads" }] });
  api.respond("GET", "/api/v1/ingestion-jobs", [job()]);
  api.respond("GET", "/api/v1/connectors", []);
  api.respond("GET", "/api/v1/knowledge-bases/:id/grants", [grant("group", "hr-staff")]);
  api.respond("GET", "/api/v1/system/security", security());
  setup?.(api);
  await page.goto(path);
  return api;
}

test.describe("the access check", () => {
  test("lists the Documents a typed Principal can and cannot read", async ({ page }) => {
    const asked: string[] = [];
    await openAccess(page, "/knowledge-bases/kb-hr?tab=access", (a) => {
      a.respondWith("GET", "/api/v1/knowledge-bases/access-check", (url) => {
        asked.push(url.search);
        return { unrestricted: false, reason: "grants", knowledge_bases: [{ id: "kb-hr", name: "HR handbook", slug: "hr-handbook" }] };
      });
      a.respondWith("GET", "/api/v1/documents/:id/access-check", (url) => {
        const restricted = url.pathname.includes("doc-salary");
        return {
          document_id: restricted ? "doc-salary" : "doc-leave",
          can_read: !restricted,
          reason: restricted ? "The caller has a grant on the knowledge base, but matches none of the document's restrictions." : "The document is unrestricted, so it inherits the knowledge base's grants.",
          knowledge_base_granted: true,
          restricted,
          restriction_matched: restricted ? false : null,
        };
      });
    });
    // The check leads the tab.
    const headings = await page.getByRole("heading", { level: 2 }).allTextContents();
    expect(headings[0]).toBe("Check what a Principal can read");

    const check = page.getByRole("button", { name: "Check access" });
    await expect(check).toBeDisabled();
    await page.getByLabel("Subject").fill("alice@corp.example");
    await page.getByLabel("Groups (optional)").fill("hr-staff");
    await check.click();

    await expect(page.getByText("alice@corp.example, groups hr-staff can retrieve from this Knowledge Base")).toBeVisible();
    await expect(page.getByRole("list", { name: "Can read" })).toContainText("Leave policy.pdf");
    const cannot = page.getByRole("list", { name: "Cannot read" });
    await expect(cannot).toContainText("Salary bands.pdf");
    await expect(cannot).toContainText("matches none of the document's restrictions");
    await expect(page.getByRole("list", { name: "Can read" })).not.toContainText("Salary bands.pdf");
    expect(asked[0]).toContain("subject=alice%40corp.example");
    expect(asked[0]).toContain("groups=hr-staff");
    await captureAll(page, "access-check");
  });

  test("says when no Grant covers the Principal", async ({ page }) => {
    await openAccess(page, "/knowledge-bases/kb-hr?tab=access", (a) => {
      a.respond("GET", "/api/v1/knowledge-bases/access-check", { unrestricted: false, reason: "grants", knowledge_bases: [] });
      a.respond("GET", "/api/v1/documents/:id/access-check", {
        document_id: "doc-leave",
        can_read: false,
        reason: "The caller holds no grant on this document's knowledge base.",
        knowledge_base_granted: false,
        restricted: false,
        restriction_matched: null,
      });
    });
    await page.getByLabel("Subject").fill("bob@corp.example");
    await page.getByRole("button", { name: "Check access" }).click();
    await expect(page.getByText("bob@corp.example cannot retrieve from this Knowledge Base")).toBeVisible();
    await expect(page.getByRole("list", { name: "Can read" })).toHaveCount(0);
    await expect(page.getByRole("list", { name: "Cannot read" })).toContainText("holds no grant");
  });

  test("shows the API's refusal", async ({ page }) => {
    await openAccess(page, "/knowledge-bases/kb-hr?tab=access", (a) => a.respond("GET", "/api/v1/knowledge-bases/access-check", { detail: "Resolver unavailable" }, 500));
    await page.getByLabel("Subject").fill("alice@corp.example");
    await page.getByRole("button", { name: "Check access" }).click();
    await expect(page.getByRole("alert").filter({ hasText: "Resolver unavailable" })).toBeVisible();
  });
});

test.describe("Grants", () => {
  test("show as typed chips with copy that they only add access", async ({ page }) => {
    await openAccess(page, "/knowledge-bases/kb-hr?tab=access", (a) =>
      a.respond("GET", "/api/v1/knowledge-bases/:id/grants", [grant("group", "hr-staff"), grant("role", "auditor"), grant("subject", "alice@corp.example"), grant("everyone", "")]),
    );
    const section = page.getByRole("region", { name: "Grants" });
    for (const chip of ["Group · hr-staff", "Role · auditor", "Subject · alice@corp.example", "Everyone"]) await expect(section.getByRole("listitem").filter({ hasText: chip })).toBeVisible();
    await expect(section).toContainText("Grants only ever add access");
    await captureAll(page, "access-grants");
  });

  test("are edited as chips and saved as the whole set", async ({ page }) => {
    let put: unknown;
    await openAccess(page, "/knowledge-bases/kb-hr?tab=access", (a) =>
      a.respondWith("PUT", "/api/v1/knowledge-bases/:id/grants", (_url, req) => {
        put = req.body;
        return [grant("role", "hr-director"), grant("group", "finance")];
      }),
    );
    const section = page.getByRole("region", { name: "Grants" });
    const save = section.getByRole("button", { name: "Save Grants" });
    await expect(save).toBeDisabled();
    await section.getByRole("button", { name: "Remove Group · hr-staff" }).click();
    await section.getByLabel("Type").selectOption("role");
    await section.getByLabel("Value").fill("hr-director");
    await section.getByRole("button", { name: "Add" }).click();
    await section.getByLabel("Type").selectOption("group");
    await section.getByLabel("Value").fill("finance");
    await section.getByLabel("Value").press("Enter");
    await save.click();
    await expect.poll(() => put).toEqual({
      grants: [
        { principal_type: "role", principal_value: "hr-director" },
        { principal_type: "group", principal_value: "finance" },
      ],
    });
    await expect(section.getByText("Saved")).toBeVisible();
  });

  test("warn that no one can read a Knowledge Base with none, only when ungranted access is denied", async ({ page }) => {
    await openAccess(page, "/knowledge-bases/kb-hr?tab=access", (a) => a.respond("GET", "/api/v1/knowledge-bases/:id/grants", []));
    await expect(page.getByText("No one has been granted this Knowledge Base")).toBeVisible();
    await captureAll(page, "access-no-grants");
  });

  test("do not warn when the platform allows ungranted access", async ({ page }) => {
    await openAccess(page, "/knowledge-bases/kb-hr?tab=access", (a) => {
      a.respond("GET", "/api/v1/knowledge-bases/:id/grants", []);
      a.respond("GET", "/api/v1/system/security", security({ ungranted_kb_access: "allow" }));
    });
    await expect(page.getByRole("region", { name: "Grants" }).getByText("No Grants.")).toBeVisible();
    await expect(page.getByText("No one has been granted this Knowledge Base")).toHaveCount(0);
  });

  test("do not warn when there are Grants", async ({ page }) => {
    await openAccess(page, "/knowledge-bases/kb-hr?tab=access");
    await expect(page.getByText("Group · hr-staff")).toBeVisible();
    await expect(page.getByText("No one has been granted this Knowledge Base")).toHaveCount(0);
  });

  test("say authentication is off instead of warning", async ({ page }) => {
    await openAccess(page, "/knowledge-bases/kb-hr?tab=access", (a) => {
      a.respond("GET", "/api/v1/knowledge-bases/:id/grants", []);
      a.respond("GET", "/api/v1/system/security", security({ auth_mode: "disabled" }));
    });
    await expect(page.getByText("Authentication is off")).toBeVisible();
    await expect(page.getByText("No one has been granted this Knowledge Base")).toHaveCount(0);
  });
});

test.describe("Document restrictions", () => {
  test("a restricted Document carries a lock in the Documents tab", async ({ page }) => {
    await openAccess(page, "/knowledge-bases/kb-hr");
    const table = page.getByRole("table", { name: "Documents" });
    await expect(table.getByRole("row", { name: /Salary bands/ })).toContainText("Restricted");
    await expect(table.getByRole("row", { name: /Leave policy/ })).not.toContainText("Restricted");
  });

  test("editing one shows the effective result against the Grants, and saves", async ({ page }) => {
    let put: unknown;
    await openAccess(page, "/knowledge-bases/kb-hr", (a) => {
      a.respond("GET", "/api/v1/documents/:id/restrictions", { document_id: "doc-salary", restrictions: [{ principal_type: "group", principal_value: "hr-leads" }], warnings: [], chunks_updated: null });
      a.respondWith("PUT", "/api/v1/documents/:id/restrictions", (_url, req) => {
        put = req.body;
        return { document_id: "doc-salary", restrictions: [{ principal_type: "group", principal_value: "hr-staff" }], warnings: [], chunks_updated: 12 };
      });
    });
    await page.getByRole("button", { name: "Restrict Salary bands.pdf" }).click();
    const editor = page.getByLabel("Effective access");
    await expect(page.getByText("Restrictions only narrow access")).toBeVisible();
    await expect(editor).toContainText("Only callers who hold a Grant and also match a restriction can read this Document");
    await expect(editor).toContainText("Not itself granted");

    // A restriction that is also a Grant reads as fully effective.
    await page.getByRole("button", { name: "Remove Group · hr-leads" }).click();
    await expect(editor).toContainText("Unrestricted: this Document inherits the Grants");
    await page.getByLabel("Value").fill("hr-staff");
    await page.getByRole("button", { name: "Add", exact: true }).click();
    await expect(editor).toContainText("Holds a Grant, so everyone it names can read");
    await captureAll(page, "access-restriction");

    await page.getByRole("button", { name: "Save restriction" }).click();
    await expect.poll(() => put).toEqual({ restrictions: [{ principal_type: "group", principal_value: "hr-staff" }] });
    await expect(page.getByText("12 indexed passages updated")).toBeVisible();
  });

  test("with no Grants, the editor says no one can read the Document yet", async ({ page }) => {
    await openAccess(page, "/knowledge-bases/kb-hr", (a) => {
      a.respond("GET", "/api/v1/knowledge-bases/:id/grants", []);
      a.respond("GET", "/api/v1/documents/:id/restrictions", { document_id: "doc-leave", restrictions: [], warnings: [], chunks_updated: null });
    });
    await page.getByRole("button", { name: "Restrict Leave policy.pdf" }).click();
    await expect(page.getByLabel("Effective access")).toContainText("cannot read this Document yet");
  });
});
