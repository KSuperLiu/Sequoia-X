import { expect, test } from "@playwright/test";

test.beforeEach(async ({ page }) => {
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const emptyPage = { items: [], total: 0, page: 1, page_size: 30 };
    const responses: Record<string, unknown> = {
      "/api/v1/auth/me": { username: "admin", csrf_token: "test-csrf" },
      "/api/v1/dashboard": { run: null, summary: null, accounts: [], data_stale: true, top_candidates: [], risk_alerts: [], recent_activity: [], latest_job: null, ready_plan_count: 0 },
      "/api/v1/accounts": [], "/api/v1/watchlist": [], "/api/v1/plans": emptyPage,
      "/api/v1/reports": [], "/api/v1/backtests": [], "/api/v1/jobs": [],
      "/api/v1/rules": [], "/api/v1/audit-logs": [], "/api/v1/backups": [],
      "/api/v1/settings": { daily_run_time: "18:30", min_market_cap: 5_000_000_000, public_base_url: "", supported_strategies: [] },
      "/api/v1/system/health": { market_cap_count: 0, market_db_size: 0, app_db_size: 0, watchlist_count: 0 },
    };
    if (path === "/api/v1/candidates/search") return route.fulfill({ json: emptyPage });
    if (path === "/api/v1/analytics/candidates") return route.fulfill({ json: [] });
    const value = responses[path];
    return value === undefined ? route.fulfill({ status: 404, json: { detail: "mock not found" } }) : route.fulfill({ json: value });
  });
});

test("核心工作台导航均可进入真实页面", async ({ page }) => {
  await page.goto("/dashboard");
  await expect(page.getByRole("heading", { name: "核心决策工作台" })).toBeVisible();
  for (const [link, heading] of [
    ["机会中心", "机会中心"], ["计划中心", "交易计划中心"], ["组合账户", "组合账户"],
    ["复盘分析", "复盘分析"], ["回测研究", "回测研究"], ["任务中心", "任务中心"], ["系统管理", "系统管理"],
  ]) {
    await page.getByRole("link", { name: link }).click();
    await expect(page.getByRole("heading", { name: heading })).toBeVisible();
  }
});
