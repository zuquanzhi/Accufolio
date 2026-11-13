<div align="center">
  <img src="assets/logo.png" alt="Accufolio logo" width="112">
  <h1>Accufolio</h1>
  <p><strong>长期投资，从容积累。</strong></p>
  <p>由 AI 整理数据的自托管投资看板，直观看见资产、配置与长期变化。</p>
  <p>
    <a href="https://github.com/zuquanzhi/Accufolio/actions/workflows/ci.yml"><img src="https://github.com/zuquanzhi/Accufolio/actions/workflows/ci.yml/badge.svg" alt="构建与测试状态"></a>
    <a href="https://github.com/zuquanzhi/Accufolio/releases/latest"><img src="https://img.shields.io/github/v/release/zuquanzhi/Accufolio" alt="最新版本"></a>
    <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-blue.svg" alt="Apache 2.0 许可证"></a>
  </p>
  <p><a href="#快速开始">快速开始</a> · <a href="#手机使用">手机使用</a> · <a href="guides/使用与部署.md">使用与部署指南</a> · <a href="https://github.com/zuquanzhi/Accufolio/releases">版本下载</a> · <a href="README.en.md">English</a></p>
</div>

---

Accufolio 面向长期定投者，将多标的持仓、投入与收益曲线、配置目标和策略回测放在同一个看板中。AI 通过本地 MCP 维护买卖记录与资产配置，应用负责查看和分析。数据保存在自己的 SQLite 数据库中，可通过浏览器、PWA 或 Android 客户端访问。

## 产品预览

![Accufolio 桌面与手机界面预览](assets/previews/product-poster.jpg)

<details>
<summary>查看更多：策略回测、行情监控、手机交易记录和 Android 欢迎页</summary>
<br>
<img src="assets/previews/mobile-pages.jpg" alt="Four app routes" width="1000">
<img src="assets/previews/mobile-allocation.png" alt="全部资产分配及目标差额" width="280">
<img src="assets/previews/web-backtest.png" alt="策略回测结果" width="800">
<img src="assets/previews/web-monitoring.png" alt="行情监控" width="800">
<img src="assets/previews/mobile-trades.png" alt="手机交易记录" width="280">
<img src="assets/previews/mobile-settings.png" alt="Mobile settings" width="280">
<img src="assets/previews/android-welcome.png" alt="Android 客户端连接服务器欢迎页" width="280">
</details>

宣传图使用实际运行界面截图和示意设备外框，交易与行情数据均为模拟数据。手机图展示响应式网页，Android 客户端连接服务器后展示同一界面；欢迎页来自 Android 15 模拟器。

## 能做什么

- **资产全貌** — 汇总多个标的的市值、持仓成本、已实现与浮动盈亏、净投入和 XIRR，以市值、成本或股数查看分布。
- **直观的变化** — 查看持仓市值、累计净投入和累计盈亏曲线，切换近一个月、三个月、一年或全部记录；触摸或键盘可读取每个日期的数据。
- **两种配置视角** — 「投资组合」查看股票持仓，「全部资产」加入 AI 填报的其他资金余额及截至日期；按市值比较当前比例与目标差额。
- **比较定投策略** — 自动获取并缓存日线，用四条策略曲线与累计投入基线比较结果；保留手动价格输入，显示来源与复权口径。
- **跟踪行情** — 涨跌幅按 L1/L2/L3 分级，结合标的与 VIX 日线计算退潮信号；数据不足时明确跳过。
- **应用外提醒** — 可选 Webhook 通知，持久化投递队列、失败重试和状态查询，让告警送达你的通知服务。
- **AI 管理数据** — MCP 支持买卖记录、其他资金与配置目标；默认只读，写工具默认返回预览，提交需开启写权限，保留审计与幂等控制。
- **跨设备访问** — 桌面浏览器、PWA 和 Android 共享同一投资看板。提供数据库备份与 JSON 导出；旧版设备记录保留只读查看与导出。

## 看板口径

| 视图 | 数据与用途 |
|---|---|
| **投资组合** | 当前股票持仓，可按市值、剩余成本或股数查看占比。股数占比只比较数量，不代表资金权重。 |
| **全部资产** | 当前股票市值 + AI 独立填报的其他资金余额，始终保留余额截至日期；不从交易推算账户现金。 |
| **配置目标** | 投资组合与全部资产分别维护目标，仅按市值计算比例和金额差额，不生成或执行订单。 |
| **历史曲线** | 用交易记录与未复权收盘价重建股票持仓；缺失数据留空，不将当前其他资金倒填历史，也不称为账户净值。 |

净投入为累计买入金额减累计卖出金额，累计盈亏为当前持仓市值减净投入。XIRR 纳入买入、卖出与期末持仓价值；样本不足或没有唯一可用解时明确显示原因。详细公式和边界见[看板与资产配置指南](guides/使用与部署.md#看板与资产配置)。

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

底部导航提供「资产」「记录」「发现」「设置」四个独立地址的页面：`/assets`、`/records`、`/discover`、`/settings`。应用内即时切换并保留位置与分析参数，直接打开或刷新地址也能进入对应页面。资产与交易记录只读，由 MCP 管理；刷新、数据来源和 Android 连接与更新入口位于设置。旧版保存在设备上的记录仍可查看、导出，不再提供手动增删改，也不会上传。


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

应用使用 HTTP Basic 认证，公开网络访问时必须通过 HTTPS；请保护好密码和数据库备份。MCP 以本地进程运行，默认只读，写入需要显式开启。旧版设备记录继续保存在当前设备、当前站点的浏览器存储中，不会上传或包含在服务器备份里；清除浏览器数据或卸载应用可能导致丢失，可先导出 JSON 留存。界面仍需连接服务器加载，暂不支持独立离线启动。更多说明见[部署与 MCP 指南](guides/使用与部署.md)。

## 适用范围

当前版本专注单用户、USD 投资看板，不连接券商或执行交易。分红、手续费、多币种和券商同步暂不支持。其他资金是独立填报的余额快照，不能替代完整现金账本。新浪日线为未复权价格，不包含分红再投资，拆股可能影响历史曲线与回测；缓存过期或源数据滞后会明确提示。Webhook 使用通用 JSON，需要兼容的接收端或转换服务，配置见[指南](guides/使用与部署.md#webhook-告警)。行情可能延迟或不可用，历史表现不代表未来收益。

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
