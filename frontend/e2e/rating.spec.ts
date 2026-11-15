import { expect, test, type Page } from "@playwright/test";
import { stubApi } from "./support/api-stub";
import { captureAll } from "./support/screenshots";

const FEEDBACK = "/api/v1/chat/conversations/:id/messages/:id/feedback";

type Sent = { rating: string; comment: string | null; share_with_admins: boolean };

/** An answered question with a feedback endpoint that remembers what was sent. */
async function answered(page: Page) {
  const api = await stubApi(page, "end-user");
  const sent: Sent[] = [];
  let removed = 0;
  api.respondWith("PUT", FEEDBACK, (_url, req) => {
    sent.push(req.body as Sent);
    return req.body;
  });
  api.intercept("DELETE", FEEDBACK, (route) => {
    removed += 1;
    return route.fulfill({ status: 204, headers: { "access-control-allow-origin": "*" } });
  });
  await page.goto("/chat");
  await page.getByRole("textbox", { name: "Your question" }).fill("How much leave do I get?");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(page.getByText(/You get 25 days of annual leave/)).toBeVisible();
  const rating = page.getByRole("group", { name: "Rate this answer" });
  const share = rating.getByRole("switch", { name: "Share this exchange with Admins" });
  return { api, sent, removed: () => removed, rating, share };
}

test("rates an answer with a comment, and nothing is shared unless the toggle is on", async ({ page }) => {
  const { sent, rating, share } = await answered(page);

  await rating.getByRole("button", { name: "Good answer" }).click();
  await expect(rating.getByText("Admins will see only this question and its answer")).toBeVisible();
  await expect(share).not.toBeChecked();
  await captureAll(page, "rating-open");
  await rating.getByRole("textbox", { name: "Comment" }).fill("Clear and quick.");
  await rating.getByRole("button", { name: "Save rating" }).click();

  await expect(rating.getByRole("button", { name: "Good answer" })).toHaveAttribute("aria-pressed", "true");
  await expect(rating.getByText("Shared with Admins")).toHaveCount(0);
  expect(sent).toEqual([{ rating: "up", comment: "Clear and quick.", share_with_admins: false }]);
});

test("the toggle starts off on every Rating, even when reopened", async ({ page }) => {
  const { sent, rating, share } = await answered(page);

  await rating.getByRole("button", { name: "Good answer" }).click();
  await rating.getByRole("button", { name: "Cancel" }).click();
  await rating.getByRole("button", { name: "Bad answer" }).click();
  await expect(share).not.toBeChecked();
  await rating.getByRole("button", { name: "Save rating" }).click();
  await expect(rating.getByRole("button", { name: "Bad answer" })).toHaveAttribute("aria-pressed", "true");

  await rating.getByRole("button", { name: "Bad answer" }).click();
  await expect(share).not.toBeChecked();
  expect(sent.map((s) => s.share_with_admins)).toEqual([false]);
});

test("shares an exchange only when the End user turns the toggle on, and marks it shared", async ({ page }) => {
  const { sent, rating, share } = await answered(page);

  await rating.getByRole("button", { name: "Bad answer" }).click();
  await share.click();
  await expect(share).toBeChecked();
  await rating.getByRole("button", { name: "Save rating" }).click();

  await expect(rating.getByText("Shared with Admins")).toBeVisible();
  expect(sent).toEqual([{ rating: "down", comment: null, share_with_admins: true }]);
  await captureAll(page, "rating-shared");
});

test("changes a Rating and removes it", async ({ page }) => {
  const { sent, removed, rating } = await answered(page);

  await rating.getByRole("button", { name: "Good answer" }).click();
  await rating.getByRole("button", { name: "Save rating" }).click();
  await rating.getByRole("button", { name: "Bad answer" }).click();
  await rating.getByRole("textbox", { name: "Comment" }).fill("Out of date.");
  await rating.getByRole("button", { name: "Save rating" }).click();
  await expect(rating.getByRole("button", { name: "Bad answer" })).toHaveAttribute("aria-pressed", "true");
  await expect(rating.getByRole("button", { name: "Good answer" })).toHaveAttribute("aria-pressed", "false");
  expect(sent.map((s) => s.rating)).toEqual(["up", "down"]);

  await rating.getByRole("button", { name: "Bad answer" }).click();
  await rating.getByRole("button", { name: "Remove rating" }).click();
  await expect(rating.getByRole("button", { name: "Bad answer" })).toHaveAttribute("aria-pressed", "false");
  expect(removed()).toBe(1);
});
