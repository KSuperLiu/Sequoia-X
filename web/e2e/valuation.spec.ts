import { expect, test } from "@playwright/test";

test("管理员可查看并修订股票三情景估值", async ({ page }) => {
  let submitted: Record<string, unknown> | null = null;
  const valuation = {
    symbol: "600519",
    market_cap: 100_000_000_000,
    fundamental: {
      report_period: "2025-12-31",
      announcement_date: "2026-04-17",
      revenue_ttm: 20_000_000_000,
      net_profit_ttm: 5_000_000_000,
      eps_ttm: 5,
      roe: 0.21,
      gross_margin: 0.91,
      net_margin: 0.5,
      yoy_net_profit: 0.2,
      liability_to_asset: 0.18,
      cfo_to_net_profit: 1.05,
      ps_ttm: 5,
      peg: 1.5,
      source: "baostock_quarterly",
    },
    history: [
      { date: "2026-08-01", pe_ttm: 10, pb_mrq: 1 },
      { date: "2026-08-04", pe_ttm: 20, pb_mrq: 2 },
      { date: "2026-08-05", pe_ttm: 30, pb_mrq: 3 },
    ],
    active_case: {
      id: 1,
      version: 1,
      method: "PE",
      forecast_period: "2026E",
      forecast_value: 5,
      bear_multiple: 15,
      base_multiple: 20,
      bull_multiple: 25,
      thesis: "稳定增长",
      catalysts: ["提价"],
      risks: ["需求下降"],
      source_note: "管理层指引",
      created_by: "system:auto",
      created_at: "2026-08-05T10:00:00Z",
    },
    result: {
      as_of_date: "2026-08-05",
      current_price: 100,
      bear_target: 75,
      base_target: 100,
      bull_target: 125,
      margin_of_safety: 0,
      valuation_zone: "FAIR_LOW",
      pe_percentile: 100,
      pb_percentile: 100,
      pe_sample_count: 3,
      pb_sample_count: 3,
    },
  };
  const stock = {
    symbol: "600519",
    profile: { name: "贵州茅台", industry: "白酒" },
    snapshot: { close: 100, pe_ttm: 30, pb_mrq: 3, date: "2026-08-05", trade_status: 1 },
    bars: [],
    candidates: [],
    fills: [],
    positions: [],
    valuation,
  };

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/v1/auth/me") {
      return route.fulfill({ json: { username: "admin", role: "ADMIN", csrf_token: "test-csrf" } });
    }
    if (path === "/api/v1/stocks/600519/valuation-cases" && request.method() === "POST") {
      submitted = request.postDataJSON();
      return route.fulfill({ status: 201, json: valuation });
    }
    if (path === "/api/v1/stocks/600519") return route.fulfill({ json: stock });
    return route.fulfill({ status: 404, json: { detail: "mock not found" } });
  });

  await page.goto("/stocks/600519");
  await expect(page.getByRole("heading", { name: "基本面估值" })).toBeVisible();
  await expect(page.getByText("市盈率相对盈利增速")).toBeVisible();
  await expect(page.getByText("净资产的赚钱效率")).toBeVisible();
  await expect(page.getByText("净利润较去年同期的增减")).toBeVisible();
  const profitGrowth = page.locator(".valuation-metrics > div").filter({ hasText: "净利同比" });
  await expect(profitGrowth.locator("b")).toHaveCSS("color", "rgb(229, 72, 64)");
  await expect(page.getByText("系统参考", { exact: true })).toBeVisible();
  await expect(page.getByText(/系统自动生成/)).toBeVisible();
  await expect(page.getByText("合理偏低")).toBeVisible();
  await expect(page.getByText("估值基准日 2026-08-05")).toBeVisible();
  await expect(page.getByText("历史估值位置")).toBeVisible();

  await page.getByRole("button", { name: "修订估值" }).click();
  await expect(page.getByText("保存后将生成v2")).toBeVisible();
  await page.getByLabel("基准倍数").fill("22");
  await page.getByLabel("预测来源与备注").fill("2026年中期研究更新");
  await page.getByRole("button", { name: "保存为新版本" }).click();

  await expect(page.getByText("估值新版本已创建")).toBeVisible();
  expect(submitted?.base_multiple).toBe(22);
  expect(submitted?.source_note).toBe("2026年中期研究更新");
});

test("任务中心提供季度财务刷新入口", async ({ page }) => {
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/v1/auth/me") {
      return route.fulfill({ json: { username: "admin", role: "ADMIN", csrf_token: "test-csrf" } });
    }
    if (path === "/api/v1/jobs") return route.fulfill({ json: [] });
    return route.fulfill({ status: 404, json: { detail: "mock not found" } });
  });

  await page.goto("/tasks");
  await expect(page.getByRole("heading", { name: "刷新季度财务" })).toBeVisible();
  await expect(page.getByText("更新候选、自选和持仓股财务及近两年 PE/PB 历史")).toBeVisible();
});
