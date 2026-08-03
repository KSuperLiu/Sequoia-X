import { expect, test } from "@playwright/test";

test("复盘分析的位置分组显示中文", async ({ page }) => {
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/v1/auth/me") {
      return route.fulfill({ json: { username: "admin", role: "ADMIN", csrf_token: "test-csrf" } });
    }
    if (path === "/api/v1/reports") return route.fulfill({ json: [] });
    if (path === "/api/v1/analytics/candidates") {
      return route.fulfill({ json: ["RIGHT", "VETO", "MIDDLE", "LEFT"].map((label, index) => ({
        label, count: 10 - index, avg_1d: 0, avg_5d: 0, avg_20d: null,
        avg_mfe: 0, avg_mae: 0, entry_rate: 0, stop_rate: 0, win_rate: 0,
      })) });
    }
    return route.fulfill({ status: 404, json: { detail: "mock not found" } });
  });

  await page.goto("/reports");
  const table = page.getByRole("table");
  for (const label of ["右侧", "否决", "中部", "左侧"]) {
    await expect(table.getByText(label, { exact: true })).toBeVisible();
  }
  for (const label of ["RIGHT", "VETO", "MIDDLE", "LEFT"]) {
    await expect(table.getByText(label, { exact: true })).toHaveCount(0);
  }
});

test("复盘分析的置信等级显示中文", async ({ page }) => {
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/v1/auth/me") {
      return route.fulfill({ json: { username: "admin", role: "ADMIN", csrf_token: "test-csrf" } });
    }
    if (path === "/api/v1/reports") return route.fulfill({ json: [] });
    if (path === "/api/v1/analytics/candidates") {
      return route.fulfill({ json: ["HIGH", "MEDIUM", "LOW"].map((label, index) => ({
        label, count: 10 - index, avg_1d: 0, avg_5d: 0, avg_20d: null,
        avg_mfe: 0, avg_mae: 0, entry_rate: 0, stop_rate: 0, win_rate: 0,
      })) });
    }
    return route.fulfill({ status: 404, json: { detail: "mock not found" } });
  });

  await page.goto("/reports");
  await page.getByRole("combobox").selectOption("confidence");
  const table = page.getByRole("table");
  for (const label of ["高", "中", "低"]) {
    await expect(table.getByText(label, { exact: true })).toBeVisible();
  }
  for (const label of ["HIGH", "MEDIUM", "LOW"]) {
    await expect(table.getByText(label, { exact: true })).toHaveCount(0);
  }
});
