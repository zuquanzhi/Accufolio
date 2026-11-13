<div align="center">
  <img src="assets/logo.png" alt="Accufolio logo" width="112">
  <h1>Accufolio</h1>
  <p><strong>长期投资，从容积累。</strong></p>
  <p>自托管的投资账本，帮你记录每一次投入，看清长期投资的进度。</p>
  <p>
    <a href="https://github.com/zuquanzhi/Accufolio/actions/workflows/ci.yml"><img src="https://github.com/zuquanzhi/Accufolio/actions/workflows/ci.yml/badge.svg" alt="构建与测试状态"></a>
    <a href="https://github.com/zuquanzhi/Accufolio/releases/latest"><img src="https://img.shields.io/github/v/release/zuquanzhi/Accufolio" alt="最新版本"></a>
    <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-blue.svg" alt="Apache 2.0 许可证"></a>
  </p>
  <p><a href="#快速开始">快速开始</a> · <a href="#手机使用">手机使用</a> · <a href="guides/使用与部署.md">使用与部署指南</a> · <a href="https://github.com/zuquanzhi/Accufolio/releases">版本下载</a> · <a href="README.en.md">English</a></p>
</div>

---

Accufolio 面向长期定投者，提供买卖记账、持仓分析、策略回测与行情监控。它是单用户、自托管应用：投资账户保存在自己的 SQLite 数据库中，可通过浏览器、PWA 或 Android 客户端访问。

## 产品预览

![Accufolio 桌面与手机界面预览](assets/previews/product-poster.jpg)

<details>
<summary>查看更多：策略回测、行情监控、手机交易记录和 Android 欢迎页</summary>
<br>
<img src="assets/previews/mobile-pages.jpg" alt="Four app routes" width="1000">
<img src="assets/previews/web-backtest.png" alt="策略回测结果" width="800">
<img src="assets/previews/web-monitoring.png" alt="行情监控" width="800">
<img src="assets/previews/mobile-trades.png" alt="手机交易记录" width="280">
<img src="assets/previews/mobile-settings.png" alt="Mobile settings" width="280">
<img src="assets/previews/android-welcome.png" alt="Android 客户端连接服务器欢迎页" width="280">
</details>

宣传图使用实际运行界面截图和示意设备外框，交易与行情数据均为模拟数据。手机图展示响应式网页，Android 客户端连接服务器后展示同一界面；欢迎页来自 Android 15 模拟器。

## 能做什么

- **记录与分析** — 通过 MCP 维护买卖记录，查看持仓成本、均价、市值、盈亏和 XIRR；交易修改可审计、可撤销。
- **比较定投策略** — 选择标的和日期，自动获取并缓存日线，比较四种投入策略；保留手动数据入口，显示数据来源与复权口径。
- **跟踪行情** — 涨跌幅按 L1/L2/L3 分级，结合标的与 VIX 日线计算退潮信号；数据不足时明确跳过。
- **应用外提醒** — 可选 Webhook 通知，持久化投递队列、失败重试和状态查询，让告警送达你的通知服务。
- **掌控数据** — SQLite 本地存储、每日和手动备份、JSON 交易导出；支持本地 MCP 接入，默认只读。
- **跨设备访问** — 桌面浏览器、手机 PWA 和 Android APK 可只读查看投资账户，也可在设备上维护独立的随手记。

## 快速开始

需要 [Docker 和 Docker Compose](https://docs.docker.com/get-docker/)。在终端执行：

```bash
git clone https://github.com/zuquanzhi/Accufolio.git
cd Accufolio
cp .env.example .env
# 编辑 .env，设置唯一且足够强的 ACCUFOLIO_PASSWORD
docker compose up -d --build
```

打开 <http://127.0.0.1:8000>，使用默认用户名 `admin` 和刚设置的密码登录。没有默认密码。容器默认只监听本机，数据库保存在 Docker 卷中。

也可以使用 Python 3.11–3.13 运行。远程访问和手机连接需要先配置 HTTPS；完整步骤、环境变量、升级及备份恢复请参阅 **[使用与部署指南](guides/使用与部署.md)**。

## 手机使用

底部导航提供「资产」「记录」「发现」「设置」四个独立地址的页面：`/assets`、`/records`、`/discover`、`/settings`。应用内即时切换并保留位置与草稿，直接打开或刷新地址也能进入对应页面。「投资账户」用于查看 AI 管理的数据，「随手记」用于手动记录，仅保存在当前设备。数据来源切换和刷新位于设置，Android 的连接地址与应用更新也从设置进入。


| 方式 | 适用情况 | 开始使用 |
|---|---|---|
| **Android APK** | Android 10 及以上 | 从 [最新 Release](https://github.com/zuquanzhi/Accufolio/releases/latest) 的 Assets 下载 `.apk`，安装后输入自托管实例的 HTTPS 根地址。 |
| **PWA** | Android 或 iOS 浏览器 | 打开 HTTPS 站点，通过浏览器菜单安装或添加到主屏幕。 |

APK 和 PWA 均需先部署服务器；APK 不内置后端。服务器更新后，网页功能会自动检测并加载；有未保存输入时会先提示。从 v1.2.0 起，Android 客户端可在应用内检查、下载并验证更新，再由系统提示确认安装；首次从旧版本升级仍需安装一次新版 APK。Release 中的源码 ZIP 用于部署和开发，并非手机安装包；同时提供 SHA-256 校验文件。

## 工作原理

```text
浏览器 / PWA / Android 客户端
              │ HTTPS
              ▼
       FastAPI + Web 界面 ── 行情提供方
              │
        SQLite 数据库与备份
              ▲
       本地 MCP 客户端（可选）
```

应用使用 HTTP Basic 认证，公开网络访问时必须通过 HTTPS；请保护好密码和数据库备份。MCP 以本地进程运行，默认只读，写入需要显式开启。随手记只保存在当前设备、当前站点的浏览器存储中，不会同步，也不包含在服务器备份里；清除浏览器数据或卸载应用可能导致丢失。随手记需要先连接服务器加载界面，暂不支持独立离线启动。更多说明见[部署与 MCP 指南](guides/使用与部署.md)。

## 适用范围

当前版本专注单用户、USD 买卖账本，不连接券商或执行交易。分红、手续费、多币种和券商同步暂不支持。新浪日线为未复权价格，不包含分红再投资，拆股可能影响回测；缓存过期或源数据滞后会明确提示。Webhook 使用通用 JSON，需要兼容的接收端或转换服务，配置见[指南](guides/使用与部署.md#webhook-告警)。行情可能延迟或不可用，历史表现不代表未来收益。

后续需求与不做的范围见[路线图](guides/roadmap.md)。

## 开发与验证

```bash
python -m venv .venv
# macOS / Linux: source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m unittest discover -s tests -v
```

CI 覆盖 Python 测试、浏览器流程、Docker 启动和 Android 测试；发布工作流验证通过后生成签名 APK、源码包及校验文件。浏览器回归、Android 构建、回测口径和贡献运行方式见[指南](guides/使用与部署.md)。欢迎通过 [Issues](https://github.com/zuquanzhi/Accufolio/issues) 反馈问题或提出建议。

## 许可证

[Apache License 2.0](LICENSE)
