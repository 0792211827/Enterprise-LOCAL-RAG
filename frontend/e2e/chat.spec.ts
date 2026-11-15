import { expect, test, type Page } from "@playwright/test";
import { stubApi, type ApiStub } from "./support/api-stub";
import { captureAll, VIEWPORTS } from "./support/screenshots";

const MARKDOWN_ANSWER = [
  "## Leave overview",
  "",
  "You get **25 days** of leave. Plans:",
  "",
  "- Annual leave",
  "- Sick leave",
  "",
  "| Plan | Days | A very long column heading that forces the table to be wider than a phone | Another very long column heading to widen things |",
  "| --- | --- | --- | --- |",
  "| Annual | 25 | Carried over up to five days into the next calendar year | Requires manager approval in the HR system |",
  "",
  "```",
  "const days = 25; // a long line of code that is definitely wider than the reading column on a phone screen",
  "```",
].join("\n");

const ask = async (page: Page, question: string) => {
  await page.getByRole("textbox", { name: "Your question" }).fill(question);
  await page.getByRole("button", { name: "Send" }).click();
};

const conversation = (id: string, title: string, daysAgo = 0) => {
  const when = new Date();
  when.setDate(when.getDate() - daysAgo);
  if (daysAgo > 0) when.setHours(12, 0, 0, 0);
  const at = when.toISOString();
  return { id, application_id: "app-policies", title, created_at: at, last_activity_at: at, message_count: 2 };
};

async function openChat(page: Page, stub?: (s: ApiStub) => void | Promise<void>) {
  const api = await stubApi(page, "end-user");
  await stub?.(api);
  await page.goto("/chat");
  return api;
}

test.describe("choosing an Application", () => {
  test("shows each Application's own name and description in the switcher", async ({ page }) => {
    await openChat(page);
    await expect(page.getByRole("heading", { name: "HR policies", exact: true })).toBeVisible();
    await page.getByRole("button", { name: /HR policies/ }).click();
    const options = page.getByRole("option");
    await expect(options).toHaveCount(2);
    await expect(options.nth(0)).toContainText("Leave, expenses and conduct.");
    await expect(options.nth(1)).toContainText("Finance desk");
    await expect(options.nth(1)).toContainText("Budgets and approvals.");
    await captureAll(page, "chat-switcher");
  });

  test("pre-selects the Application used last", async ({ page }) => {
    await openChat(page);
    await page.getByRole("button", { name: /HR policies/ }).click();
    await page.getByRole("option", { name: /Finance desk/ }).click();
    await expect(page.getByRole("heading", { name: "Finance desk", exact: true })).toBeVisible();

    await page.reload();
    await expect(page.getByRole("heading", { name: "Finance desk", exact: true })).toBeVisible();
  });

  test("tells an End user with nothing available who to ask", async ({ page }) => {
    await openChat(page, (s) => s.respond("GET", "/api/v1/chat/applications", []));
    await expect(page.getByText("Nothing has been made available to you yet")).toBeVisible();
    await expect(page.getByText(/ask an administrator/i)).toBeVisible();
    await expect(page.getByRole("textbox", { name: "Your question" })).toHaveCount(0);
  });
});

test.describe("asking and reading an answer", () => {
  test("streams an answer with no bubbles and offers to copy it", async ({ page, context }) => {
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
    const api = await openChat(page);
    await ask(page, "How much leave do I get?");

    await expect(page.getByText("How much leave do I get?")).toBeVisible();
    await expect(page.getByText(/You get 25 days of annual leave/)).toBeVisible();
    expect(api.calls).toContain("POST /api/v1/chat/conversations");
    await expect(page.getByRole("textbox", { name: "Your question" })).toHaveValue("");

    await page.getByRole("button", { name: "Copy answer" }).click();
    await expect(page.getByText("Copied")).toBeVisible();
    expect(await page.evaluate(() => navigator.clipboard.readText())).toContain("You get 25 days of annual leave");
    await captureAll(page, "chat-answer");
  });

  test("Enter sends and Shift+Enter breaks the line", async ({ page }) => {
    await openChat(page);
    const box = page.getByRole("textbox", { name: "Your question" });
    await box.fill("first line");
    await box.press("Shift+Enter");
    await box.pressSequentially("second line");
    await expect(box).toHaveValue("first line\nsecond line");
    await box.press("Enter");
    await expect(page.getByText(/You get 25 days of annual leave/)).toBeVisible();
    await expect(box).toHaveValue("");
  });

  test("shows a quiet Thinking state and lets the End user stop", async ({ page }) => {
    let release!: () => void;
    const held = new Promise<void>((resolve) => (release = resolve));
    await openChat(page, (s) => s.streamAnswer({ text: "Late answer.", until: held }));
    await ask(page, "Slow question");

    await expect(page.getByText("Thinking")).toBeVisible();
    await expect(page.getByRole("button", { name: "Send" })).toHaveCount(0);
    await page.getByRole("button", { name: "Stop" }).click();

    await expect(page.getByText("Thinking")).toHaveCount(0);
    await expect(page.getByText("Stopped")).toBeVisible();
    await expect(page.getByRole("textbox", { name: "Your question" })).toBeEnabled();
    release();
    await expect(page.getByText("Late answer.")).toHaveCount(0);
  });

  test("shows a plain error and retries the same question", async ({ page }) => {
    const api = await openChat(page);
    api.streamAnswers([
      { status: 500, detail: "The model is not answering." },
      { text: "Second time lucky." },
    ]);
    await ask(page, "Will this work?");

    await expect(page.getByText("The model is not answering.")).toBeVisible();
    await page.getByRole("button", { name: "Retry" }).click();
    await expect(page.getByText("Second time lucky.")).toBeVisible();
    await expect(page.getByText("The model is not answering.")).toHaveCount(0);
    // The question is asked once, not typed again and not duplicated.
    await expect(page.getByText("Will this work?")).toHaveCount(1);
  });

  test("keeps what streamed and offers a retry when the stream reports an error", async ({ page }) => {
    const api = await openChat(page);
    api.streamAnswers([{ text: "Half an answ", error: "The model stopped part-way." }, { text: "Whole answer." }]);
    await ask(page, "Tell me");

    await expect(page.getByText("Half an answ")).toBeVisible();
    await expect(page.getByText("The model stopped part-way.")).toBeVisible();
    await page.getByRole("button", { name: "Retry" }).click();
    await expect(page.getByText("Whole answer.")).toBeVisible();
  });

  test("renders markdown, and wide content scrolls inside the column", async ({ page }) => {
    await openChat(page, (s) => s.streamAnswer({ text: MARKDOWN_ANSWER }));
    await page.setViewportSize(VIEWPORTS.phone);
    await ask(page, "Summarise leave");

    await expect(page.getByRole("heading", { name: "Leave overview" })).toBeVisible();
    await expect(page.getByText("25 days", { exact: true })).toBeVisible();
    await expect(page.getByRole("listitem").filter({ hasText: "Sick leave" })).toBeVisible();
    await expect(page.getByRole("table")).toBeVisible();
    await expect(page.getByRole("columnheader", { name: "Plan" })).toBeVisible();
    await expect(page.getByRole("cell", { name: "Annual", exact: true })).toBeVisible();
    await expect(page.getByText(/const days = 25;/)).toBeVisible();

    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow).toBeLessThanOrEqual(0);
    await captureAll(page, "chat-markdown");
  });

  test("shows a raw HTML tag as text and never runs it", async ({ page }) => {
    await openChat(page, (s) =>
      s.streamAnswer({
        text: 'Before <b>bold</b> <img src="x" onerror="window.__pwned = 1"> <script>window.__pwned = 2</script> after.',
      }),
    );
    await ask(page, "Try to inject");

    await expect(page.getByText(/<b>bold<\/b>/)).toBeVisible();
    await expect(page.getByText(/<script>/)).toBeVisible();
    await page.waitForTimeout(300);
    expect(await page.evaluate(() => (window as unknown as { __pwned?: number }).__pwned)).toBeUndefined();
    await expect(page.locator("main b")).toHaveCount(0);
    await expect(page.locator("main img")).toHaveCount(0);
  });

  test("does not fetch images an answer points at", async ({ page }) => {
    const requested: string[] = [];
    page.on("request", (r) => requested.push(r.url()));
    await openChat(page, (s) => s.streamAnswer({ text: "Look: ![chart](http://tracker.example.test/pixel.png) done." }));
    await ask(page, "Image please");
    await expect(page.getByText(/done\./)).toBeVisible();
    expect(requested.filter((u) => u.includes("tracker.example.test"))).toEqual([]);
  });

  test("tells the End user when the Application is no longer available", async ({ page }) => {
    const api = await openChat(page);
    api.streamAnswer({ status: 404, detail: "Application not found" });
    await ask(page, "Hello?");
    await expect(page.getByText("HR policies is no longer available to you.")).toBeVisible();
  });
});

test.describe("Conversations", () => {
  const history = [
    conversation("c-today", "Leave carry-over", 0),
    conversation("c-yesterday", "Expense limits", 1),
    conversation("c-old", "Onboarding checklist", 90),
  ];
  const detail = {
    ...history[0],
    messages: [
      { id: "m1", role: "user", content: "Can I carry leave over?", citations: [], created_at: history[0].created_at, feedback: null, attachments: [] },
      { id: "m2", role: "assistant", content: "Up to five days carry over.", citations: [], created_at: history[0].created_at, feedback: null, attachments: [] },
    ],
    pending_attachments: [],
  };
  const rail = (page: Page) => page.getByRole("complementary", { name: "Conversation history" });

  test("lists Conversations grouped by recency with privacy copy", async ({ page }) => {
    await openChat(page, (s) => s.respond("GET", "/api/v1/chat/conversations", history));
    const list = rail(page);
    for (const group of ["Today", "Yesterday", "Older"]) await expect(list.getByRole("heading", { name: group })).toBeVisible();
    await expect(list.getByRole("button", { name: "Leave carry-over", exact: true })).toBeVisible();
    await expect(list.getByText(/only you can read your conversations/i)).toBeVisible();
    await captureAll(page, "chat-rail");
  });

  test("opens a Conversation at its latest message", async ({ page }) => {
    await openChat(page, (s) => {
      s.respond("GET", "/api/v1/chat/conversations", history);
      s.respond("GET", "/api/v1/chat/conversations/c-today", detail);
    });
    await rail(page).getByRole("button", { name: "Leave carry-over", exact: true }).click();
    await expect(page.getByText("Can I carry leave over?")).toBeVisible();
    await expect(page.getByText("Up to five days carry over.")).toBeVisible();
  });

  test("starts a new Conversation in one click", async ({ page }) => {
    await openChat(page, (s) => {
      s.respond("GET", "/api/v1/chat/conversations", history);
      s.respond("GET", "/api/v1/chat/conversations/c-today", detail);
    });
    await rail(page).getByRole("button", { name: "Leave carry-over", exact: true }).click();
    await expect(page.getByText("Up to five days carry over.")).toBeVisible();
    await page.getByRole("button", { name: "New chat" }).first().click();
    await expect(page.getByText("Up to five days carry over.")).toHaveCount(0);
  });

  test("renames a Conversation", async ({ page }) => {
    const api = await openChat(page, (s) => s.respond("GET", "/api/v1/chat/conversations", history));
    let renamed = "";
    api.respondWith("PATCH", "/api/v1/chat/conversations/c-today", (_url, req) => {
      renamed = (req.body as { title: string }).title;
      return { ...history[0], title: renamed };
    });
    await rail(page).getByRole("button", { name: "Rename Leave carry-over" }).click();
    const field = page.getByRole("textbox", { name: "Conversation name" });
    await field.fill("Carry-over rules");
    await field.press("Enter");
    await expect.poll(() => renamed).toBe("Carry-over rules");
  });

  test("deletes a Conversation only after a confirmation", async ({ page }) => {
    const api = await openChat(page, (s) => s.respond("GET", "/api/v1/chat/conversations", history));
    api.respond("DELETE", "/api/v1/chat/conversations/c-yesterday", undefined, 204);
    await rail(page).getByRole("button", { name: "Delete Expense limits" }).click();

    const dialog = page.getByRole("dialog", { name: /Delete this conversation/ });
    await expect(dialog).toContainText("Expense limits");
    await dialog.getByRole("button", { name: "Cancel" }).click();
    expect(api.calls).not.toContain("DELETE /api/v1/chat/conversations/c-yesterday");

    api.respond("GET", "/api/v1/chat/conversations", [history[0], history[2]]);
    await rail(page).getByRole("button", { name: "Delete Expense limits" }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Delete" }).click();
    await expect.poll(() => api.calls).toContain("DELETE /api/v1/chat/conversations/c-yesterday");
    await expect(rail(page).getByRole("button", { name: "Expense limits", exact: true })).toHaveCount(0);
  });

  test("says so when a Conversation cannot be opened", async ({ page }) => {
    await openChat(page, (s) => {
      s.respond("GET", "/api/v1/chat/conversations", history);
      s.respond("GET", "/api/v1/chat/conversations/c-today", { detail: "Conversation not found" }, 404);
    });
    await rail(page).getByRole("button", { name: "Leave carry-over", exact: true }).click();
    await expect(page.getByText("Conversation not found")).toBeVisible();
  });

  test("collapses the rail behind a button on a phone and works one-handed", async ({ page }) => {
    await page.setViewportSize(VIEWPORTS.phone);
    await openChat(page, (s) => {
      s.respond("GET", "/api/v1/chat/conversations", history);
      s.respond("GET", "/api/v1/chat/conversations/c-today", detail);
    });
    await expect(rail(page)).toHaveCount(0);
    await page.getByRole("button", { name: "Conversations" }).click();
    const drawer = page.getByRole("dialog", { name: "Conversations" });
    await drawer.getByRole("button", { name: "Leave carry-over", exact: true }).click();
    await expect(drawer).toHaveCount(0);
    await expect(page.getByText("Up to five days carry over.")).toBeVisible();

    await ask(page, "And sick leave?");
    await expect(page.getByText(/You get 25 days of annual leave/)).toBeVisible();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow).toBeLessThanOrEqual(0);
    await captureAll(page, "chat-phone");
  });
});
