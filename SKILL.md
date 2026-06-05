# Agent Browser v1.1.0

AI 可操控的持久化 Chrome 浏览器。

## 调用方式

```
py skills/agent-browser/scripts/agent_browser.py <action> [args...]
```

## 命令

| 命令 | 说明 | 示例 |
|------|------|------|
| `goto <url>` | 打开网页 | `goto https://example.com` |
| `state` | 列出交互元素（保存到 state.json） | `state` |
| `click <n>` | 按 state 编号点击（3 级 fallback：Playwright → JS → href 导航） | `click 3` |
| `click "<sel>"` | CSS 选择器点击 | `click "#login-btn"` |
| `click "<sel>" --wait-nav` | 点击并等待页面导航（用于 SPA） | `click "a.btn" --wait-nav` |
| `type "<text>"` | 输入到焦点元素 | `type "hello"` |
| `type "<sel>" "<text>"` | 输入到指定元素 | `type "#search" "关键词"` |
| `press <key>` | 按键 | `press Enter` |
| `screenshot [name]` | 截图 (jpg) | `screenshot inbox` |
| `extract [sel]` | 提取文本 | `extract div.content` |
| `html [sel]` | 提取 HTML | `html table.lvw` |
| `scroll <up/down> [px]` | 滚动 | `scroll down 500` |
| `wait <ms|sel>` | 等待 | `wait 2000` |
| `eval <js>` | 执行 JS | `eval document.title` |
| `tabs list` | 标签页列表 | `tabs list` |
| `tabs switch <n>` | 切换标签页 | `tabs switch 1` |
| `tabs new` | 新标签页 | `tabs new` |
| `close` | 关闭浏览器 | `close` |

## Do 模式（链式执行）

```
# 从 JSON 文件执行多步链
py agent_browser.py do plan.json

# 从断点续跑（跳过前 N 步）
py agent_browser.py do plan.json --resume-from=3
```

JSON 格式：
```json
[
  {"action": "goto", "args": ["https://example.com"]},
  {"action": "state"},
  {"action": "click", "args": ["3"]},
  {"action": "wait", "args": ["2000"]},
  {"action": "extract"}
]
```

## Watch 模式协议

```
# 启动（后台运行）
py skills/agent-browser/scripts/agent_browser.py watch

# 发送命令（JSON 到 stdin）
{"action": "goto", "args": ["https://example.com"]}
{"action": "state"}
{"action": "click", "args": ["3"]}
{"action": "screenshot", "args": ["test"]}
{"action": "exit"}
```

## 状态文件

- `scripts/state.json` — 当前交互元素列表（state 命令更新）
- `user_data/` — Chrome profile（cookie/session 持久化）
- `logs/YYYY-MM-DD/commands.jsonl` — 每日指令日志

## 特殊动作

| 动作 | 说明 |
|------|------|
| `download <url> [save_path]` | 下载文件（直接 URL 或 CSS 选择器触发） |
| `manual <message>` | 暂停等待人工介入，完成后创建 `.manual_done` 信号文件 |
| `mouse_click <x> <y>` | 绝对坐标点击 |
| `eval_iframe <js>` | 在第一个 iframe 内执行 JS |

## 登录处理

遇到 SSO/验证码时，浏览器会弹出窗口。你手动登录后，cookie 自动保存到 `user_data/`，后续操作复用登录态。

## 安全

- 不访问 file:// / localhost / 内网地址
- 密码字段内容不记录日志
- `user_data/` 和 `logs/` 已在 .gitignore 中
