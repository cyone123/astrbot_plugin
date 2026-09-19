# AstrBot Minecraft 更新推送插件 (astrbot_plugin_mc_update)

> 🎮 自动检测 Minecraft 版本更新，通过 LLM 提炼中文速报并推送到群聊，全面支持 **QQ 官方机器人** 及多平台适配。

[![AstrBot](https://img.shields.io/badge/AstrBot-Plugin-blue.svg)](https://github.com/AstrBotDevs/AstrBot)
[![License](https://img.shields.io/badge/license-GPL--3.0-green.svg)](LICENSE)

---

## ✨ 核心特性

- 🔄 **实时自动检测**：基于 Mojang 官方 Version Manifest V2 API 定时轮询，第一时间获知正式版及快照版发布。
- 📰 **官方更新日志抓取**：自动从 Mojang 官方 Zendesk 支持中心及 Minecraft Wiki 获取最新更新说明。
- 🤖 **LLM 智能速报**：调用 AstrBot 接入的 AI 大模型，将冗长的更新日志提炼为结构化、生动活泼的 400 字中文更新速报。
- 🐧 **QQ 官方机器人深度兼容**：
  - 基于 `unified_msg_origin`（UMO）机制，无缝支持 QQ 开放平台机器人的 `group_openid` 会话。
  - 精简排版，规避 QQ 官方平台对复杂 Markdown 或富媒体的不兼容问题，确保推送触达率。
- ⚙️ **灵活配置**：
  - 支持在 AstrBot WebUI 中可视化配置轮询间隔、快照推送开关、指定总结大模型等。
  - 支持群内直接使用指令快捷订阅或取消订阅。

---

## 📌 支持指令

所有指令均支持在群聊或私聊中直接发送：

| 指令 | 说明 | 示例 |
| --- | --- | --- |
| `/mc help` | 查看插件指令帮助与当前群订阅状态 | `/mc help` |
| `/mc sub` | 订阅当前群聊的 Minecraft 更新推送 | `/mc sub` |
| `/mc unsub` | 取消当前群聊的 Minecraft 更新推送 | `/mc unsub` |
| `/mc check` | 立即手动检查一次是否有新版本 | `/mc check` |
| `/mc latest` | 获取当前最新正式版的 AI 总结速报 | `/mc latest` |
| `/mc latest snapshot` | 获取当前最新快照版的 AI 总结速报 | `/mc latest snapshot` |

---

## ⚙️ 插件配置 (_conf_schema.json)

安装插件后，可在 AstrBot 管理面板（WebUI）中进行可视化配置：

| 配置项 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `check_interval` | 整数 (int) | `1800` | 更新检查轮询间隔（秒），默认 30 分钟 |
| `notify_snapshot` | 布尔 (bool) | `false` | 是否推送快照版 / 预览版（默认仅推送正式版） |
| `max_content_chars` | 整数 (int) | `2500` | 送入 LLM 的最大日志字符数（约 500~600 Tokens，控制消耗） |
| `chat_provider_id` | 提供商选取 | `""` | 用于总结的 LLM 提供商，留空则使用当前默认模型 |
| `custom_prompt` | 文本 (text) | 见下文 | 自定义大模型总结提示词模板 |
| `subscribers` | 列表 (list) | `[]` | 已订阅推送的会话 UMO 列表（可在群内通过 `/mc sub` 自动登记） |

### 默认 Prompt 模板
```text
你是一个 Minecraft 资讯播报助手。请根据以下 Minecraft 更新日志内容，用生动友好、结构清晰的中文写一份版本更新速报。
要求：
1. 突出版本号与版本类型（正式版/快照版）。
2. 提炼出 3-5 点最核心、玩家最关心的更新亮点或重要改动。
3. 如果有重要 Bug 修复或技术调整，用简明语言概括 1-2 条。
4. 总体字数控制在 400 字以内，排版精美，适当使用 Emoji。

更新日志内容：
{changelog}
```

---

## 🚀 QQ 官方机器人使用注意事项

1. **群订阅机制**：
   - QQ 官方机器人的群聊没有传统的数字群号，而是使用加密字符串 `group_openid`。
   - 只需在需要接收推送的 QQ 群中，@机器人 发送 `/mc sub`，插件即可自动获取并持久化该群的会话标识。
2. **主动推送配额**：
   - QQ 开放平台对机器人的主动群消息有频次与配额限制，请合理设置轮询间隔（推荐保持默认的 30 分钟），避免因频繁触发被平台限流。
3. **文本安全与审核**：
   - 本插件对 LLM 总结输出做了长度与排版优化，确保符合 QQ 官方机器人的消息格式规范。

---

## 📄 开源许可

本项目基于 GNU General Public License v3.0 开源。
