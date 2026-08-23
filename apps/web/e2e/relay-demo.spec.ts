import { test, expect } from "@playwright/test";

test.describe("Relay demo", () => {
  test.skip(!process.env.RELAY_E2E_BASE_URL, "Set RELAY_E2E_BASE_URL for an authenticated demo environment");

  test("shows the demo outcome honestly", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Action outcomes" })).toBeVisible();
    await expect(page.getByText(/completed|ride booked/i)).toHaveCount(0);
  });
});
