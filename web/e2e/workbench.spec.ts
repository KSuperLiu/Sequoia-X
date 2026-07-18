import { expect, test } from "@playwright/test";

test.beforeEach(async ({ page }) => {
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const emptyPage = { items: [], total: 0, page: 1, page_size: 30 };
    const responses: Record<string, unknown> = {
      "/api/v1/auth/me": { username: "admin", role: "ADMIN", csrf_token: "test-csrf" },
      "/api/v1/dashboard": { run: null, summary: null, accounts: [], data_stale: true, top_candidates: [], risk_alerts: [], recent_activity: [], latest_job: null, ready_plan_count: 0 },
      "/api/v1/accounts": [], "/api/v1/watchlist": [], "/api/v1/plans": emptyPage,
      "/api/v1/reports": [], "/api/v1/backtests": [], "/api/v1/jobs": [],
      "/api/v1/rules": [], "/api/v1/audit-logs": [], "/api/v1/backups": [],
      "/api/v1/settings": { daily_run_time: "18:30", min_market_cap: 5_000_000_000, public_base_url: "", supported_strategies: [] },
      "/api/v1/system/health": { market_cap_count: 0, market_db_size: 0, app_db_size: 0, watchlist_count: 0 },
    };
    if (path === "/api/v1/candidates/search") return route.fulfill({ json: emptyPage });
    if (path === "/api/v1/stocks/688321") return route.fulfill({ json: { symbol: "688321", profile: { name: "微芯生物", industry: "医药制造业" }, snapshot: { close: 24.88, date: "2026-07-16", trade_status: 1 }, bars: [], candidates: [], fills: [], positions: [] } });
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

test("游客可浏览数据且看不到管理员功能", async ({ page }) => {
  await page.route("**/api/v1/auth/me", (route) => route.fulfill({ status: 401, json: { detail: "未登录" } }));

  await page.goto("/dashboard");
  await expect(page.getByRole("heading", { name: "核心决策工作台" })).toBeVisible();
  await expect(page.getByText("游客浏览")).toBeVisible();
  await expect(page.getByTitle("登录或注册").first()).toBeVisible();
  await expect(page.getByRole("link", { name: "任务中心" })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "系统管理" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "更新数据" })).toHaveCount(0);

  await page.goto("/settings");
  await expect(page).toHaveURL(/\/dashboard$/);
});

test("游客可直接注册个人账号并进入模拟交易工作台", async ({ page }) => {
  await page.route("**/api/v1/auth/me", (route) => route.fulfill({ status: 401, json: { detail: "未登录" } }));
  await page.route("**/api/v1/auth/register", (route) => route.fulfill({ status: 201, json: { username: "demo_user", role: "MEMBER", csrf_token: "member-csrf" } }));

  await page.goto("/login");
  await page.getByRole("button", { name: "注册", exact: true }).click();
  await page.getByLabel("账号 ID").fill("demo_user");
  await page.getByLabel("密码", { exact: true }).fill("demo-password");
  await page.getByLabel("确认密码").fill("demo-password");
  await page.getByRole("button", { name: "注册并登录" }).click();

  await expect(page).toHaveURL(/\/dashboard$/);
  await expect(page.getByText("demo_user").first()).toBeVisible();
  await expect(page.getByText("个人账号")).toBeVisible();
  await expect(page.getByRole("link", { name: "系统管理" })).toHaveCount(0);
});

test("首页机会指标卡跳转并带入候选位置筛选", async ({ page }) => {
  await page.goto("/dashboard");

  await page.getByRole("button", { name: /左侧机会/ }).click();
  await expect(page).toHaveURL(/\/candidates\?zone=LEFT$/);
  await expect(page.getByLabel("位置筛选")).toHaveValue("LEFT");

  await page.goto("/dashboard");
  await page.getByRole("button", { name: /中部机会/ }).click();
  await expect(page).toHaveURL(/\/candidates\?zone=MIDDLE$/);
  await expect(page.getByLabel("位置筛选")).toHaveValue("MIDDLE");

  await page.goto("/dashboard");
  await page.getByRole("button", { name: /右侧 \/ 否决/ }).click();
  await expect(page).toHaveURL(/\/candidates\?zone=RIGHT(%2C|,)VETO$/);
  await expect(page.getByLabel("位置筛选")).toHaveValue("RIGHT,VETO");

  await page.goto("/dashboard");
  await page.getByRole("button", { name: /覆盖标的/ }).click();
  await expect(page).toHaveURL(/\/candidates$/);
  await expect(page.getByLabel("位置筛选")).toHaveValue("");
});

test("股票详情名称链接到对应雪球行情页", async ({ page }) => {
  await page.goto("/stocks/688321");
  const link = page.getByRole("link", { name: "在雪球查看微芯生物" });
  await expect(link).toHaveAttribute("href", "https://xueqiu.com/S/SH688321");
  await expect(link).toHaveAttribute("target", "_blank");
  await expect(link).toHaveAttribute("rel", "noopener noreferrer");
});

test("网页手动任务可以从任务中心中止", async ({ page }) => {
  let status = "PENDING";
  let cancelCalled = false;
  const job = { id: 7, job_type: "DAILY_UPDATE", source: "MANUAL", requested_by: "admin", requested_at: "2026-07-18T01:00:00Z", progress_current: 0, progress_total: 0, message: "已加入队列" };
  await page.route("**/api/v1/jobs**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/v1/jobs/7/cancel" && request.method() === "POST") {
      cancelCalled = true;
      status = "CANCELLED";
      return route.fulfill({ json: { ...job, status, exit_code: -15 } });
    }
    if (path === "/api/v1/jobs") return route.fulfill({ json: [{ ...job, status }] });
    return route.fallback();
  });
  page.on("dialog", (dialog) => dialog.accept());

  await page.goto("/tasks");
  await page.getByRole("button", { name: "中止" }).click();
  await expect(page.getByText("等待任务已取消")).toBeVisible();
  expect(cancelCalled).toBe(true);
});
