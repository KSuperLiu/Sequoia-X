# Sequoia-X: 王者回归 | The King Returns

> A 股量化选股系统 V2 | A-Share Quantitative Stock Selection System V2

---

## 简介 | Introduction

Sequoia-X V2 是面向 A 股市场的量化选股系统，基于现代 Python 工程化标准从零重构。
系统以 OOP 架构、向量化计算和增量数据更新为核心设计原则，每日收盘后自动选股并推送至飞书群。

数据层使用 [baostock](http://baostock.com)（免费、无需注册、无限流）拉取历史及增量日 K 数据（后复权），
存储于本地 SQLite，彻底规避东方财富反爬问题。

---

## 两种运行模式

```bash
python main.py               # 日常模式：8进程增量补数据 + 跑策略 + 飞书推送（2~3分钟）
python main.py --backfill     # 回填模式：按本地市值门槛回填历史K线
```

## Web 交易追踪系统

Web 系统在原有选股链路之上提供四维评分、真实价交易计划、候选追踪、模拟/实盘账本、HTML 日报和基础回测。历史行情仍存放在 `data/sequoia_v2.db`，新增业务数据独立存放在 `data/sequoia_app.db`。

### 本机开发启动

先在 `.env` 中至少配置：

```dotenv
ADMIN_USERNAME=admin
ADMIN_PASSWORD=请设置强密码
COOKIE_SECURE=false
PUBLIC_BASE_URL=http://127.0.0.1:5173
```

安装 Python 和前端依赖：

```powershell
uv sync --extra dev
cd web
npm install
cd ..
```

打开两个 PowerShell 窗口：

```powershell
# 窗口 1：FastAPI
.\.venv\Scripts\python.exe -m uvicorn sequoia_x.app.api:app --host 127.0.0.1 --port 8000
```

```powershell
# 窗口 2：React/Vite
cd web
npm run dev
```

浏览器访问 `http://127.0.0.1:5173`，使用 `ADMIN_USERNAME` 和 `ADMIN_PASSWORD` 登录。

### Docker 公网部署

Linux 服务器安装 Docker 后，在 `.env` 中将 `COOKIE_SECURE` 设为 `true`，配置真实的 `DOMAIN`、`PUBLIC_BASE_URL`、管理员强密码和飞书 Webhook，然后执行：

```bash
sudo chown -R 10001:10001 data logs backups
docker compose up -d --build
```

Compose 会启动 Caddy HTTPS、FastAPI 和独立日程 Worker。日程默认在交易日 18:30 运行；应用库每日备份 30 份，行情库每周备份 4 份。

### 常用地址

- API 健康检查：`/health`
- OpenAPI 文档：设置 `ENABLE_API_DOCS=true` 后访问 `/docs`；公网默认关闭
- Web API 前缀：`/api/v1`

---

## 内置策略 | Strategies

| 策略 | 说明 |
|---|---|
| **TurtleTrade** | 海龟突破：20日新高 + 成交额过亿 + 阳线防诱多，按涨幅排序 |
| **MaVolume** | 均线+放量突破 |
| **HighTightFlag** | 高而窄的旗形整理突破 |
| **LimitUpShakeout** | 涨停洗盘回踩确认 |
| **UptrendLimitDown** | 上升趋势中的跌停反包 |
| **RpsBreakout** | 欧奈尔 RPS 相对强度突破 |

---

## 快速开始 | Quick Start

### 环境要求

- Python >= 3.10

### 1. 安装依赖

```bash
# 推荐使用 uv（快速包管理器）
uv sync

# 或者 pip
pip install .
```

### 2. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，填写飞书 Webhook URL
```

### 3. 首次回填历史数据

```bash
python main.py --backfill
```

约 12 分钟完成 ~5200 只 A 股历史后复权日 K 数据回填。

### 4. 日常运行

```bash
python main.py
```

建议配合 crontab 每个交易日收盘后自动执行：

```cron
15 19 * * 1-5 cd /root/Sequoia-X && .venv/bin/python main.py >> log.txt 2>&1
```

---

## 目录结构 | Project Structure

```
Sequoia-X/
├── main.py                      # 入口：argparse 分发日常/回填模式
├── pyproject.toml               # 依赖声明 + ruff/pytest 配置
├── .env.example                 # 环境变量模板
├── data/                        # SQLite 数据库（运行时生成，不入 git）
├── sequoia_x/
│   ├── core/
│   │   ├── config.py            # Pydantic-settings 配置管理
│   │   └── logger.py            # rich 结构化日志
│   ├── data/
│   │   └── engine.py            # 数据引擎（baostock 回填 + 增量同步 + SQLite）
│   ├── strategy/
│   │   ├── base.py              # 策略抽象基类
│   │   ├── turtle_trade.py      # 海龟交易策略
│   │   ├── ma_volume.py         # 均线放量策略
│   │   ├── high_tight_flag.py   # 高窄旗形策略
│   │   ├── limit_up_shakeout.py # 涨停洗盘策略
│   │   ├── uptrend_limit_down.py # 上升跌停策略
│   │   └── rps_breakout.py      # RPS 突破策略
│   └── notify/
│       └── feishu.py            # 飞书 Webhook 推送
└── tests/                       # 属性测试（hypothesis）
```

---

## 数据说明

- **数据源**：[baostock](http://baostock.com)（免费、无需注册、无限流）
- **复权方式**：后复权（hfq）— 历史价格不变，适合增量存储，避免除权导致数据错乱
- **存储**：本地 SQLite（`data/sequoia_v2.db`），可直接拷贝到其他机器使用
- **日常增量**：8 进程并行通过 baostock 拉取，2~3 分钟完成全市场更新

---

## 许可证 | License

MIT
