<div align="center">
  <img src="assets/logo.png" alt="Accufolio logo" width="112">
  <h1>Accufolio</h1>
  <p><strong>Agent-driven asset allocation.</strong></p>
  <p>A self-hosted dashboard. Let your agent maintain the data; see your allocation clearly.</p>
  <p>
    <a href="https://github.com/zuquanzhi/Accufolio/actions/workflows/ci.yml"><img src="https://github.com/zuquanzhi/Accufolio/actions/workflows/ci.yml/badge.svg" alt="Build and test status"></a>
    <a href="https://github.com/zuquanzhi/Accufolio/releases/latest"><img src="https://img.shields.io/github/v/release/zuquanzhi/Accufolio" alt="Latest release"></a>
    <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-blue.svg" alt="Apache 2.0 license"></a>
  </p>
  <p><a href="#how-it-works">How it works</a> · <a href="#quick-start">Quick start</a> · <a href="guides/使用与部署.md">Deployment & MCP guide</a> · <a href="https://github.com/zuquanzhi/Accufolio/releases">Releases</a> · <a href="README.md">简体中文</a></p>
</div>

---

Accufolio is a single-user, self-hosted asset allocation dashboard. Give your agent completed transactions, dated fund balances, and allocation targets. The agent maintains those records through MCP; Accufolio turns them into holding breakdowns, target gaps, and historical curves.

Asset and transaction data is read-only in Web, PWA, and Android, backed by your own SQLite database. Accufolio provides MCP tools and visualization. You choose and run the agent; the app does not include an AI model or a chat service.

## Preview

![Accufolio desktop and mobile preview](assets/previews/product-poster.jpg)

<details>
<summary>More views: allocation, strategy curves, monitoring, and mobile access</summary>
<br>
<img src="assets/previews/mobile-pages.jpg" alt="Four app routes" width="1000">
<img src="assets/previews/mobile-allocation.png" alt="Total assets allocation and target gaps" width="280">
<img src="assets/previews/web-backtest.png" alt="Strategy backtest results" width="800">
<img src="assets/previews/web-monitoring.png" alt="Price monitoring" width="800">
<img src="assets/previews/mobile-trades.png" alt="Trades on mobile" width="280">
<img src="assets/previews/mobile-settings.png" alt="Mobile settings" width="280">
<img src="assets/previews/android-welcome.png" alt="Android client server connection welcome screen" width="280">
</details>

The device frames are illustrative; the screenshots show the running application with synthetic transactions and prices in an isolated database. The mobile images show the responsive web interface; the Android client displays the same interface after connecting to a server; the welcome screen is captured on an Android 15 emulator.

## Allocation at a glance

- **Agent-maintained assets:** update buys, sells, other funds, and targets through MCP. Trade creation, editing, deletion, and allocation replacement support previews, explicit write permissions, audit history, and retry protection.
- **Current distribution:** aggregate market value, cost, realized and unrealized gains, net investment, and XIRR. Explore holdings by market value, remaining cost, or share count.
- **Target comparison:** maintain separate targets for the investment portfolio and total assets. See current market-value weights and monetary gaps.
- **Long-term perspective:** switch periods on holdings-value, net-investment, and cumulative-profit curves. Touch and keyboard controls expose individual dates; missing observations remain gaps.
- **Strategy comparisons:** fetch cached daily prices and compare four contribution strategies against a cumulative-investment baseline, with expandable ending results.
- **Ongoing monitoring:** L1/L2/L3 movement alerts, a calm signal based on stock and VIX closes, and optional Webhook delivery with retries.

## How it works

```text
Your transactions, balances, and targets
                 │
                 ▼
       Your agent / MCP client
                 │ Local stdio · permission-controlled access
                 ▼
          Accufolio MCP ──→ SQLite database
                                  │
     Quotes & daily prices ──→ FastAPI analytics + Web
                                  │ HTTPS
                                  ▼
                         Web · PWA · Android
                     Read-only assets and records
```

The agent reads existing records and configuration before preparing changes. Trade creation, editing, deletion, and allocation replacement default to `dry_run=true`. Submitting these operations requires `ACCUFOLIO_MCP_SCOPE=write` on the MCP process and `dry_run=false`. Allocation updates also check the current revision to avoid overwriting a newer change.

Once MCP is connected, you can give your agent completed transactions, balance reports, and targets like these:

> Record a completed trade: I bought 2 shares of QQQM on 2026-11-12 at 280 USD per share. Show me a preview before committing.

> My other-funds balance was 5,000 USD as of 2026-11-14. Read the current allocation plan, preserve both target sets and its note, and preview a replacement of only the balance and date.

> Preview these targets: QQQM 60% and SPY 40% for my investment portfolio; QQQM 45%, SPY 30%, and other funds 25% for total assets. Preserve the reported balance, date, and note, then wait for confirmation before committing.

Balances come from information you provide and must be explicitly updated by the agent. Buys and sells never automatically change other funds; salary, spending, and transfers require a fresh balance report. MCP currently runs over **local stdio with access to the same database**, not a remote MCP endpoint. The phone connects to the Web service. See the [MCP setup guide](guides/使用与部署.md#mcp-接入) for configuration (Chinese).

## Views and calculations

| View | Data and purpose |
|---|---|
| **Investment portfolio** | Current stock holdings, weighted by market value, remaining cost, or share count. Share weights compare quantities, not capital allocation. |
| **Total assets** | Current stock market value plus an independently reported USD other-funds balance, with its as-of date. An unreported balance is unknown, not zero. |
| **Allocation targets** | Separate targets for the portfolio and total assets. Gaps use market value only; they do not create or execute orders. |
| **History** | Stock holdings reconstructed from transactions and unadjusted closing prices. Missing values remain gaps; today's other-funds balance is not inserted into history. This is not account NAV. |

Net investment equals cumulative purchases minus sale proceeds. Cumulative profit equals holdings value minus net investment. XIRR uses buys, sells, and terminal holdings value; short samples and cash flows without a unique supported solution show an explicit reason. See the [calculation and allocation guide](guides/使用与部署.md#看板与资产配置) for details (Chinese).

## Quick start

Deploy the dashboard, then connect an agent that can access the same database. Start the service with [Docker and Docker Compose](https://docs.docker.com/get-docker/):

```bash
git clone https://github.com/zuquanzhi/Accufolio.git
cd Accufolio
cp .env.example .env
# Edit .env and set a strong, unique ACCUFOLIO_PASSWORD
docker compose up -d --build
```

Visit <http://127.0.0.1:8000> and sign in with username `admin` and the password you set. There is no default password or seeded portfolio. The container binds to localhost by default and stores the database in a Docker volume.

Next, follow the [MCP setup guide](guides/使用与部署.md#mcp-接入) to launch `scripts/mcp_server.py` from your MCP client. Its `ACCUFOLIO_DB` must point to the same database used by the Web service. Docker deployments need an accessible shared database path for the local MCP process; pointing it at another empty database will not connect it to your dashboard. Start with read-only tools and enable write scope when you are ready to submit asset data.

Ask the agent to read `get_dashboard` and `get_allocation_plan`, then preview your first records using the information you provide. Web and mobile will read the updated dashboard. Python 3.11–3.13 deployment, HTTPS, backups, and upgrades are covered in the [usage guide](guides/使用与部署.md).

## Mobile access

Assets, Records, Discover, and Settings switch instantly while preserving scroll position and analysis parameters. Asset and transaction records are read-only. Connection, refresh, and update controls live in Settings.


| Option | Requirements | Getting started |
|---|---|---|
| **Android APK** | Android 10 or later | Download the `.apk` from the [latest release](https://github.com/zuquanzhi/Accufolio/releases/latest), install it, and enter your server's HTTPS origin. |
| **PWA** | Android or iOS browser | Open your HTTPS site and use the browser's install or add-to-home-screen action. |

Both options require a deployed server; the APK does not include the backend. After the server is updated, web changes are detected and loaded automatically, with a prompt when there is unsaved input. From v1.2.0, the Android app can check, download and verify updates in-app, then request system installation confirmation. Upgrading from an older version requires installing the new APK once. The release source ZIP is for deployment and development, not an Android installer. SHA-256 checksum files are provided with release assets.

## Your data, your storage

The server provides daily and manual SQLite backups and JSON trade exports. The Web service uses HTTP Basic authentication and requires HTTPS for remote access. MCP inherits database access from the local process running it; the Web password does not replace operating-system permissions.

Older on-device records remain on their original device and browser origin, available for viewing and export only. They are not uploaded, merged into the investment portfolio, or included in server backups. Export them before clearing browser data or uninstalling the app. The server is still required to load the interface; independent offline startup is not supported.

## Scope and limitations

The current release focuses on single-user asset allocation in USD. It does not connect to a broker or place orders. Dividends, fees, multiple currencies, and broker sync are not supported. Other funds are a reported balance snapshot, not a complete cash ledger. Sina daily closes are unadjusted: splits can distort historical curves and backtests, and dividends are not reinvested. Stale data is visibly flagged. Webhooks use generic JSON and require a compatible receiver or adapter; configuration is covered in the [guide](guides/使用与部署.md#webhook-告警). Quotes may be delayed or unavailable; historical results do not guarantee future returns. Planned work and exclusions are tracked in the [roadmap](guides/roadmap.md).

## Development

```bash
python -m venv .venv
# macOS / Linux: source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m unittest discover -s tests -v
```

CI covers Python tests, browser flows, Docker startup, and Android checks. The release workflow produces a signed APK, a source archive, and checksum files after verification. See the [guide](guides/使用与部署.md) for browser and Android testing. Bug reports and suggestions are welcome via [Issues](https://github.com/zuquanzhi/Accufolio/issues).

## License

[Apache License 2.0](LICENSE)
