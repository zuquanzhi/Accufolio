# Accufolio v1.1.0 — Android APK

## 下载哪个文件？

- **Accufolio-1.1.0.apk**：Android 安装包，支持 Android 10 及以上。
- **Accufolio-v1.1.0.zip**：服务端与客户端源码，需要 Python 或 Docker 部署。
- **SHA256SUMS**：以上文件的 SHA-256 校验值。

安装 APK 后，输入自己的 Accufolio HTTPS 服务器根地址并登录。APK 不内置后端服务，需要先部署服务器。网页功能随服务端更新自动加载；原生壳升级仍需系统确认安装新版 APK。

新增固定签名的 Android 客户端、服务器配置、HTTP 认证、交易文件导出、HTTPS 同源限制、返回与刷新操作。发布流水线检查签名、单元测试和 Android lint，并通过 Android 15 模拟器安装启动及 HTTP 地址拒绝测试。实体手机兼容性尚未验证。

v1.0.0 的持仓、回测、监控、审计、备份和 MCP 功能继续保留。此前 v1.0.0 只有源码/PWA，本次补齐 APK 交付。
