import { expect, test } from "@playwright/test";

test("K 线止损标签显示完整价格和距最新价百分比", async ({ page }) => {
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/v1/auth/me") {
      return route.fulfill({ json: { username: "admin", role: "ADMIN", csrf_token: "test-csrf" } });
    }
    if (path === "/api/v1/stocks/600000") {
      return route.fulfill({ json: {
        symbol: "600000",
        profile: { name: "浦发银行", industry: "银行业" },
        snapshot: { close: 19.44, date: "2026-08-03", trade_status: 1 },
        bars: Array.from({ length: 60 }, (_, index) => ({
          date: `2026-05-${String(index + 1).padStart(2, "0")}`,
          open: 19 + index * 0.01,
          high: 19.3 + index * 0.01,
          low: 18.4 + index * 0.01,
          close: 19.05 + index * 0.0065,
          volume: 1_000_000 + index * 10_000,
        })),
        candidates: [{
          id: 1, trade_date: "2026-08-03", strategies: [], total_score: 6,
          consensus_count: 1, confidence: "MEDIUM", zone: "LEFT", current_zone: "LEFT",
          plan_status: "READY", entry_low: 19, entry_high: 19.6,
          stop_price: 18.7, current_entry_low: 19, current_entry_high: 19.6,
          current_stop_price: 18.7, rationale: "测试止损标签",
        }],
        fills: [], positions: [],
      } });
    }
    return route.fulfill({ status: 404, json: { detail: "mock not found" } });
  });

  await page.goto("/stocks/600000");
  const chart = page.getByRole("img", { name: "股票K线图，止损 18.70 · 距最新价 -3.8%" });
  await expect(chart).toBeVisible();
  await expect(chart).toHaveAttribute("aria-label", "股票K线图，止损 18.70 · 距最新价 -3.8%");
  const stopLabel = page.locator(".stock-chart-stop-label");
  await expect(stopLabel).toHaveCount(0);
  const chartBox = await chart.boundingBox();
  expect(chartBox).not.toBeNull();
  await page.mouse.move((chartBox?.x || 0) + (chartBox?.width || 0) / 2, (chartBox?.y || 0) + 120);
  for (const label of ["开盘", "收盘", "最低", "最高"]) {
    await expect(page.getByText(label, { exact: true })).toBeVisible();
  }
  for (const label of ["open", "close", "lowest", "highest"]) {
    await expect(page.getByText(label, { exact: true })).toHaveCount(0);
  }
  await page.mouse.move((chartBox?.x || 0) + (chartBox?.width || 0) - 100, (chartBox?.y || 0) + 238);
  await expect(stopLabel).toHaveText("止损 18.70 · 距最新价 -3.8%");
  await page.mouse.move((chartBox?.x || 0) + 100, (chartBox?.y || 0) + 60);
  await expect(stopLabel).toHaveCount(0);
  await page.mouse.click((chartBox?.x || 0) + (chartBox?.width || 0) - 100, (chartBox?.y || 0) + 238);
  await expect(stopLabel).toBeVisible();
  await page.mouse.move((chartBox?.x || 0) + 100, (chartBox?.y || 0) + 60);
  await expect(stopLabel).toBeVisible();
  await page.mouse.click((chartBox?.x || 0) + (chartBox?.width || 0) - 100, (chartBox?.y || 0) + 238);
  await page.mouse.move((chartBox?.x || 0) + 100, (chartBox?.y || 0) + 60);
  await expect(stopLabel).toHaveCount(0);
});
