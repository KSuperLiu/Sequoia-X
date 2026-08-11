import { expect, test } from "@playwright/test";

test.beforeEach(async ({ page }) => {
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const emptyPage = { items: [], total: 0, page: 1, page_size: 30 };
    const responses: Record<string, unknown> = {
      "/api/v1/auth/me": { username: "admin", role: "ADMIN", csrf_token: "test-csrf" },
      "/api/v1/dashboard": { run: null, summary: null, accounts: [], data_stale: true, top_candidates: [], risk_alerts: [], recent_activity: [], latest_job: null, ready_plan_count: 0 },
      "/api/v1/accounts": [], "/api/v1/watchlist": [], "/api/v1/plans": emptyPage,
      "/api/v1/journal": { ...emptyPage, page_size: 12, stats: { total: 0, completed: 0, current_month: 0, avg_discipline: 0 }, tags: [] },
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
    ["复盘分析", "复盘分析"], ["复盘日记", "个人复盘日记"], ["回测研究", "回测研究"], ["任务中心", "任务中心"], ["系统管理", "系统管理"],
  ]) {
    await page.getByRole("link", { name: link }).click();
    await expect(page.getByRole("heading", { name: heading })).toBeVisible();
  }
});

test("个人复盘日记可使用模板并保存完成状态", async ({ page }) => {
  let saved: Record<string, unknown> | null = null;
  const context = {
    report: { id: 3, title: "7 月 22 日系统日报", summary: { action: "控制仓位，等待左侧机会" } },
    candidate: { count: 12, avg_score: 5.8, left_count: 2, middle_count: 3, right_count: 6, veto_count: 1 },
    fills: [], snapshots: [],
  };
  await page.route("**/api/v1/journal**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path.startsWith("/api/v1/journal/context/")) return route.fulfill({ json: context });
    if (path === "/api/v1/journal" && request.method() === "POST") {
      saved = request.postDataJSON();
      return route.fulfill({ status: 201, json: { id: 9 } });
    }
    if (path === "/api/v1/journal/9") return route.fulfill({ json: { id: 9, ...(saved || {}), created_at: "2026-07-22T10:00:00Z", updated_at: "2026-07-22T10:00:00Z", context } });
    return route.fallback();
  });

  await page.goto("/journal/new");
  await expect(page.getByRole("heading", { name: "新建个人复盘" })).toBeVisible();
  await expect(page.getByText("候选").first()).toBeVisible();
  await page.getByRole("button", { name: "使用模板" }).click();
  await expect(page.getByLabel("市场观察")).toContainText("指数与成交量");
  await page.getByLabel("标题").fill("今日只做计划内交易");
  await page.getByText("纪律执行评分").locator("..").getByRole("button", { name: "5" }).click();
  await page.getByLabel("标签").fill("纪律, 震荡");
  await page.getByLabel("关联股票").fill("600519 000001");
  await page.getByRole("button", { name: "完成复盘" }).click();

  await expect(page).toHaveURL(/\/journal\/9$/);
  await expect(page.getByText("复盘已完成")).toBeVisible();
  expect(saved?.status).toBe("COMPLETED");
  expect(saved?.discipline_score).toBe(5);
  expect(saved?.related_symbols).toEqual(["600519", "000001"]);
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

test("最低总市值以亿元显示并按元保存", async ({ page }) => {
  let submitted: Record<string, unknown> | null = null;
  await page.route("**/api/v1/settings", async (route) => {
    if (route.request().method() === "PUT") {
      submitted = route.request().postDataJSON();
      return route.fulfill({ json: { ok: true } });
    }
    return route.fallback();
  });

  await page.goto("/settings");
  const marketCap = page.getByLabel("最低总市值（亿元）");
  await expect(marketCap).toHaveValue("50");
  await marketCap.fill("55");
  await page.getByRole("button", { name: "保存", exact: true }).click();

  await expect(page.getByText("系统设置已保存")).toBeVisible();
  expect(submitted?.min_market_cap).toBe(5_500_000_000);
});

test("手机端底部菜单保持可见并可横向浏览", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/dashboard");

  const sidebar = page.locator(".sidebar");
  const topbar = page.locator(".topbar");
  const navigation = page.getByRole("navigation", { name: "主菜单" });
  await expect(sidebar).toBeVisible();
  await expect(topbar).toBeVisible();
  await expect(topbar.getByPlaceholder("搜索股票代码或名称")).toBeVisible();
  await expect(topbar.getByText("工作台", { exact: true })).toBeVisible();
  await expect(navigation).toBeVisible();
  await expect(page.getByRole("link", { name: "工作台" })).toBeVisible();

  const topbarBox = await topbar.boundingBox();
  expect(topbarBox).not.toBeNull();
  expect(Math.round(topbarBox?.y || 0)).toBe(0);
  const box = await sidebar.boundingBox();
  expect(box).not.toBeNull();
  expect(Math.round((box?.y || 0) + (box?.height || 0))).toBe(844);
  expect(await navigation.evaluate((element) => element.scrollWidth > element.clientWidth)).toBe(true);

  const settings = page.getByRole("link", { name: "系统管理" });
  await settings.evaluate((element) => element.scrollIntoView({ block: "nearest", inline: "center" }));
  await settings.click();
  await expect(page.getByRole("heading", { name: "系统管理" })).toBeVisible();
  await expect(topbar.getByText("系统管理", { exact: true })).toBeVisible();
});

test("模拟账户买入 100 股可通过浏览器校验并提交", async ({ page }) => {
  let submittedQuantity = 0;
  const account = {
    id: 1, name: "模拟组合", account_type: "PAPER", initial_cash: 1_000_000,
    portfolio: { account_id: 1, cash: 1_000_000, market_value: 0, equity: 1_000_000, total_weight: 0, unrealized_pnl: 0, realized_pnl: 0, positions: [] },
  };
  await page.route("**/api/v1/accounts**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/v1/accounts" && request.method() === "GET") return route.fulfill({ json: [account] });
    if (path === "/api/v1/accounts/1/fills" && request.method() === "POST") {
      submittedQuantity = Number(request.postDataJSON().quantity);
      return route.fulfill({ json: { warnings: [] } });
    }
    if (request.method() === "GET") return route.fulfill({ json: [] });
    return route.fallback();
  });

  await page.goto("/portfolio?symbol=600763&price=36.64");
  const quantity = page.getByLabel("数量");
  await expect(quantity).toHaveValue("100");
  expect(await quantity.evaluate((input: HTMLInputElement) => ({ min: input.min, step: input.step, valid: input.checkValidity() }))).toEqual({ min: "100", step: "100", valid: true });
  await page.getByRole("button", { name: "保存成交" }).click();
  expect(submittedQuantity).toBe(100);
});

test("机会中心提供中文策略指南和策略筛选", async ({ page }) => {
  await page.goto("/candidates");
  await expect(page.getByRole("heading", { name: "均线放量金叉" })).toHaveCount(0);
  await page.getByRole("button", { name: "策略说明" }).click();
  const guide = page.locator(".modal-wide");
  await expect(guide.getByRole("heading", { name: "选股策略说明" })).toBeVisible();
  await expect(guide.getByRole("heading", { name: "均线放量金叉" })).toBeVisible();
  await expect(guide.getByRole("heading", { name: "海龟突破（A股改良）" })).toBeVisible();
  await expect(guide.getByRole("heading", { name: "高位窄幅旗形" })).toBeVisible();
  await expect(guide.getByRole("heading", { name: "涨停后放量洗盘" })).toBeVisible();
  await expect(guide.getByRole("heading", { name: "RPS 强势临近新高" })).toBeVisible();
  await expect(guide.getByRole("heading", { name: "近期定向增发公告" })).toBeVisible();
  await expect(guide.getByText("当日成交量大于 20 日均量的 1.5 倍")).toBeVisible();
  await expect(guide.getByText(/策略只负责从市场中筛选形态，不等同于买入建议/)).toBeVisible();
  await guide.getByRole("button", { name: "关闭" }).click();

  await page.getByLabel("策略筛选").selectOption("RpsBreakoutStrategy");
  await expect(page).toHaveURL(/strategy=RpsBreakoutStrategy/);
});

test("回测表单使用中文策略名并显示所选策略说明", async ({ page }) => {
  await page.goto("/backtests");
  await page.getByRole("button", { name: "新建回测" }).click();
  const strategy = page.getByLabel("策略");
  await expect(strategy).toContainText("海龟突破（A股改良） · TurtleTradeStrategy");
  await expect(page.getByText("寻找突破近 20 日高点")).toHaveCount(0);
  await page.getByRole("button", { name: "查看当前策略说明" }).click();
  await expect(page.locator(".modal-wide").last()).toContainText("寻找突破近 20 日高点");
  await page.locator(".modal-wide").last().getByRole("button", { name: "关闭" }).click();

  await strategy.selectOption("RpsBreakoutStrategy");
  await page.getByRole("button", { name: "查看当前策略说明" }).click();
  await expect(page.locator(".modal-wide").last()).toContainText("RPS 强势临近新高");
  await expect(page.locator(".modal-wide").last()).toContainText("追高风险");
});

test("运行中的回测可以查看进度日志并提交中止请求", async ({ page }) => {
  let cancelRequested = 0;
  const run = {
    id: 12, strategy_name: "TurtleTradeStrategy", start_date: "2025-01-01",
    end_date: "2025-12-31", initial_cash: 1_000_000, status: "RUNNING",
    current_stage: "模拟交易", progress_current: 80, progress_total: 240,
  };
  await page.route("**/api/v1/backtests**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/v1/backtests/12/cancel" && request.method() === "POST") {
      cancelRequested = 1;
      return route.fulfill({ json: { ...run, cancel_requested: 1, current_stage: "正在中止" } });
    }
    if (path === "/api/v1/backtests/12") return route.fulfill({ json: {
      ...run, cancel_requested: cancelRequested,
      current_stage: cancelRequested ? "正在中止" : "模拟交易",
      logs: [{ id: 1, level: "INFO", message: "模拟进度 80/240", created_at: "2026-07-20T01:00:00Z" }],
      trades: [], equity: [],
    } });
    if (path === "/api/v1/backtests") return route.fulfill({ json: [{ ...run, cancel_requested: cancelRequested }] });
    return route.fallback();
  });
  page.on("dialog", (dialog) => dialog.accept());

  await page.goto("/backtests");
  await expect(page.getByText("模拟交易 · 80/240")).toBeVisible();
  await page.getByRole("button", { name: "日志与详情" }).click();
  await expect(page.getByText("模拟进度 80/240")).toBeVisible();
  await page.getByRole("button", { name: "中止回测" }).click();
  await expect(page.getByText("中止请求已提交")).toBeVisible();
  await expect(page.getByText("正在中止", { exact: true })).toBeVisible();
  expect(cancelRequested).toBe(1);
});

test("候选列表按 A 股习惯显示反弹红色和回撤绿色", async ({ page }) => {
  await page.route("**/api/v1/candidates/search**", (route) => route.fulfill({ json: {
    items: [{ id: 1, symbol: "600763", name: "通策医疗", industry: "C17 医疗健康服务与专科医院", market_cap: 123_400_000_000, trade_date: "2026-07-18", strategies: ["TurtleTradeStrategy"], consensus_count: 1, confidence: "MEDIUM", drawdown_60: 0.2, rebound_60: 0.15, volume_ratio: 1.5, total_score: 7, real_close: 36.64, pe_ttm: 25, current_zone: "LEFT", zone: "LEFT", current_entry_low: 35, current_entry_high: 37, current_stop_price: 34, lifecycle_status: "NEW", plan_status: "DRAFT" }],
    total: 1, page: 1, page_size: 30,
  } }));

  await page.goto("/candidates");
  const row = page.getByRole("row").filter({ hasText: "通策医疗" });
  await expect(row.locator(".market-down")).toHaveText("-20.0%");
  await expect(row.locator(".market-up")).toHaveText("+15.0%");
  await expect(row.locator(".market-down")).toHaveCSS("color", "rgb(34, 169, 107)");
  await expect(row.locator(".market-up")).toHaveCSS("color", "rgb(229, 72, 64)");
  await expect(row.locator(".negative")).toHaveCSS("color", "rgb(34, 169, 107)");
  await expect(row.getByText("1234亿")).toBeVisible();
  await expect(row.locator(".industry-label")).toHaveAttribute("title", "医疗健康服务与专科医院");
  await expect(row.locator(".industry-label")).not.toContainText("C17");
  await expect(row.locator(".industry-label")).toHaveCSS("text-overflow", "ellipsis");
});
