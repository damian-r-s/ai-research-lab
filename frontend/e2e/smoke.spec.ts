import { type Page, expect, test } from "@playwright/test";

const PASSWORD = process.env.E2E_PASSWORD ?? "";

async function signIn(page: Page, username: string) {
  await page.goto("/login");
  await page.getByLabel("Username").fill(username);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Lab status" })).toBeVisible();
}

test("unauthenticated visitors are sent to the login page", async ({ page }) => {
  await page.goto("/audit");
  await expect(page).toHaveURL(/\/login\?next=%2Faudit$/);
  await expect(page.getByRole("heading", { name: "AI Research Lab" })).toBeVisible();
});

test("wrong credentials show a clear error", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Username").fill("e2e-admin");
  await page.getByLabel("Password").fill("definitely not the password");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("alert")).toHaveText("invalid username or password");
});

test("login, live status, audit log, sign out", async ({ page, request }) => {
  await signIn(page, "e2e-admin");

  // Status shows real readings, not placeholders.
  await expect(page.getByText(/Operational|Degraded/)).toBeVisible();
  await expect(page.getByRole("meter", { name: "RAM", exact: true })).toBeVisible();
  await expect(page.getByRole("meter", { name: "Usage", exact: true })).toBeVisible();
  await expect(page.getByText(/\d+(\.\d)? GiB \/ \d+(\.\d)? GiB/).first()).toBeVisible();
  await expect(page.getByText("Reachable")).toBeVisible();
  await expect(page.getByText("Up to date")).toBeVisible();

  // Audit log: history plus a new event arriving live, without a reload.
  await page.getByRole("link", { name: "Audit log" }).first().click();
  await expect(page.getByText("Live", { exact: true })).toBeVisible();
  await expect(page.getByText("e2e-admin logged in").first()).toBeVisible();
  const intruderLines = page.getByText("Failed login for intruder (wrong credentials)");
  const before = await intruderLines.count(); // other test projects share this backend
  await request.post("/api/auth/login", {
    headers: { "X-Lab-CSRF": "1" },
    data: { username: "intruder", password: "wrong wrong wrong" },
  });
  await expect(intruderLines).toHaveCount(before + 1);

  // Sign out really ends the session.
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/login/);
  await page.goto("/");
  await expect(page).toHaveURL(/\/login/);
});

test("admin can create a user; a viewer cannot manage users", async ({ page, browser }) => {
  await signIn(page, "e2e-admin");
  await page.getByRole("link", { name: "Users" }).click();
  const name = `user-${Date.now()}`;
  await page.getByLabel("Username").fill(name);
  await page.getByLabel("Password (at least 12 characters)").fill("a sufficiently long password");
  await page.getByRole("button", { name: "Create user" }).click();
  await expect(page.getByRole("status")).toHaveText("User created.");
  await expect(page.getByRole("cell", { name, exact: true })).toBeVisible();

  const viewerContext = await browser.newContext();
  const viewer = await viewerContext.newPage();
  await signIn(viewer, "e2e-viewer");
  await expect(viewer.getByRole("link", { name: "Users" })).toHaveCount(0);
  await viewer.goto("/users");
  await expect(viewer.getByRole("alert")).toHaveText("This page requires the admin role.");
  await viewerContext.close();
});

test("security headers are sent with the page", async ({ request }) => {
  const response = await request.get("/");
  const headers = response.headers();
  expect(headers["content-security-policy"]).toContain("default-src 'self'");
  expect(headers["content-security-policy"]).toContain("frame-ancestors 'none'");
  expect(headers["x-frame-options"]).toBe("DENY");
  expect(headers["x-content-type-options"]).toBe("nosniff");
  expect(headers["server"]).toBe("nginx"); // no version disclosed
});

test("the interface contains no non-English text", async ({ page }) => {
  await signIn(page, "e2e-admin");
  for (const path of ["/", "/audit", "/users"]) {
    await page.goto(path);
    await page.waitForLoadState("networkidle").catch(() => undefined);
    const text = await page.locator("body").innerText();
    expect(text, `non-English characters on ${path}`).not.toMatch(/[ąćęłńóśźżĄĆĘŁŃÓŚŹŻ]/);
  }
});
