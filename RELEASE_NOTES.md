# Accufolio v2.4.0 — 持仓成本与盈亏对账

「投资 → 配置与持仓」现在优先展示每只证券的交易净盈亏。摊薄成本价、盈亏平衡价、已实现与未实现盈亏分别列出，跌破两条成本线时分别提示。含分红总回报仍在收益详情中查看。

Agent 可用 `get_cost_basis_calibrations` 和 `set_cost_basis_calibration` 按账户、标的和对账单日期核对券商已实现盈亏。提交前支持预览，提交时检查账户版本并写入审计。部分卖出后继续累计，全部平仓后该轮校准结束。校准只影响已实现与未实现的分配，不写入现金流水。

**API 变更**　从 v2.4.0 起，`average_price` 表示摊薄成本价。原移动加权成本总额仍保存在 `cost`，按交易计算的已实现盈亏保存在 `realized_pnl`。持仓和单标的接口均提供 `breakeven_price`、`diluted_cost`、`realized_baseline`、`net_cost`、`unrealized_pnl` 和 `net_pnl`。接口的计算说明与[收益指南](https://github.com/zuquanzhi/Accufolio/blob/v2.4.0/guides/performance.md)已更新。

原币现金余额和折算摘要在输出时处理浮点尾差。金额按展示币种格式化和着色，零钱余额归零后显示为中性零。

升级自托管服务端即可使用新页面，既有 v2 库使用原有 settings 表保存显式校准。Release Assets 提供同签名 Android 安装包 `Accufolio-2.4.0.apk`、源码包和 SHA-256 校验文件。已安装的客户端可在应用内检查原生更新，按 Android 系统提示确认覆盖安装。
