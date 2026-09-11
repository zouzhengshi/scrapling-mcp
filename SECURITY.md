# 🔐 安全说明

## 支持的版本

| 版本 | 支持情况 |
| --- | --- |
| `1.2.x` | ✅ 接收安全问题报告 |
| 其他版本 | ⚠️ 建议升级到最新版本 |

## 报告安全漏洞

请不要在公开 Issue、讨论区或聊天消息中发布漏洞细节、账号密码、Cookie、浏览器状态文件或代理凭据。

优先通过 GitHub 仓库的 **Security → Report a vulnerability** 创建私密安全报告：

<https://github.com/zouzhengshi/scrapling-mcp/security/advisories/new>

如果该入口不可用，请通过维护者 GitHub 账号 `@zouzhengshi` 私下联系，并尽量提供：

- 受影响的版本、操作系统和 Python 版本；
- 最小复现步骤或脱敏复现代码；
- 潜在影响和利用条件；
- 建议的修复方向（如有）。

## 登录态与敏感数据

- 不要把密码、验证码或 Cookie 原文发送给 AI Agent。
- `auth-profiles` 中的浏览器状态文件、Cookie profile、代理用户名和密码都应视为敏感凭据。
- 本项目默认通过本机 stdio 连接 MCP，不建议未经认证就把服务改成公网 HTTP 端口。
- 日志应保持脱敏；提交 Issue 或分享日志前，请确认没有 URL 查询参数、Cookie、Authorization 或代理凭据。
- 只抓取你有权访问的内容，并遵守目标网站的服务条款、隐私规则和适用法律。

## 安全边界

项目提供应用层的 URL/DNS 校验、进程隔离、资源限制和登录态域名过滤，但这不是操作系统级沙箱，也不替代容器、防火墙、最小权限账户或出口网络策略。

