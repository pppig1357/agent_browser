# Agent Browser v2.1.5

AI 可操控的独立 Chrome 浏览器（CDP 连接）。

## 调用方式

```
py skills/agent-browser/scripts/agent_browser.py <action> [args...]
```

## 生命周期管理

| 命令 | 说明 |
|------|------|
| `start` | 启动独立 Chrome 进程（port 9222），Chrome 存活不受 Python 进程影响 |
| `stop` | 优雅关闭 Chrome |

> 💡 `do` 和 `watch` 模式会自动检测并启动 Chrome（如未运行）。

## 命令

| 命令 | 说明 | 示例 |
|------|------|------|
| `goto <url>` | 打开网页 | `goto https://example.com` |
| `state` | 列出交互元素（保存到 state.json） | `state` |
| `click <n>` | 按 state 编号点击（3 级 fallback：Playwright → JS → href） | `click 3` |
| `click "<sel>"` | CSS 选择器点击 | `click "#login-btn"` |
| `click "<sel>" --wait-nav` | 点击并等待页面导航（用于 SPA） | `click "a.btn" --wait-nav` |
| `type "<text>"` | 输入到焦点元素 | `type "hello"` |
| `type "<sel>" "<text>"` | 输入到指定元素 | `type "#search" "关键词"` |
| `press <key>` | 按键 | `press Enter` |
| `screenshot [name]` | 截图 (jpg) | `screenshot inbox` |
| `extract [sel]` | 提取文本（或结构化提取，见下方） | `extract div.content` |
| `md [save_path]` | 页面转 Markdown | `md page.md` |
| `html [sel]` | 提取 HTML | `html table.lvw` |
| `scroll <up/down> [px]` | 滚动 | `scroll down 500` |
| `wait <ms\|sel>` | 等待毫秒或选择器出现 | `wait 2000` |
| `wait --text "..."` | 等待页面出现指定文字 | `wait --text "登录成功"` |
| `wait --js "expr"` | 等待 JS 条件成立 | `wait --js ".items.length > 5"` |
| `wait --network-idle` | 等待网络空闲 | `wait --network-idle` |
| `wait --title "..."` | 等待页面标题变化 | `wait --title "控制台"` |
| `wait --url "..."` | 等待 URL 匹配 | `wait --url "/dashboard"` |
| `eval <js>` | 执行 JS | `eval document.title` |
| `tabs list` | 标签页列表 | `tabs list` |
| `tabs switch <n>` | 切换标签页 | `tabs switch 1` |
| `tabs new` | 新标签页 | `tabs new` |
| `dblclick <n>` | 双击元素（SPA/OWA 专用） | `dblclick 3` |
| `dblclick "<sel>"` | 双击 CSS 选择器 | `dblclick ".subject"` |
| `close` | 关闭当前标签页 | `close` |

## PDF 保存 📄

Chrome 内置 PDF 查看器无法被脚本交互。`pdf_save` 通过 JS fetch 获取原始 PDF 字节并保存到本地：

```bash
# 保存当前页面
py agent_browser.py pdf_save

# 直接下载指定 PDF URL
py agent_browser.py pdf_save https://example.com/doc.pdf

# 指定保存路径
py agent_browser.py pdf_save https://example.com/doc.pdf C:\myfile.pdf
```

文件默认保存到 `downloads/` 目录。

## Markdown 转换 📝

`md` 命令将当前页面转换为 Markdown，保留标题、链接、列表、图片等结构：

```bash
# 打印到 stdout
py agent_browser.py md

# 保存到文件
py agent_browser.py md page.md
```

适用场景：提取页面正文给 AI 阅读，存档备查。

## 结构化提取 📊

从列表页批量提取字段，输出 JSON：

```bash
# CLI 模式：传入 JSON 配置
py agent_browser.py extract '{"container":"article.item","fields":{"title":"h2 a","url":"h2 a @href"},"limit":10,"output":"items.json"}'
```

**Do 链模式**（推荐）：
```json
[
  {"action": "goto", "args": ["https://news.ycombinator.com"]},
  {
    "action": "extract",
    "container": "tr.athing",
    "fields": {
      "title": ".titleline a",
      "url": ".titleline a @href",
      "site": ".sitestr"
    },
    "limit": 10,
    "output": "hn_top10.json"
  }
]
```

**字段选择器语法**：
- `"h2 a"` — 取 textContent
- `"h2 a @href"` — 取 href 属性
- `"h2 a @data-id"` — 任意属性均可

**输出**：
```json
[
  {"title": "Show HN: My Project", "url": "https://...", "site": "github.com"},
  ...
]
```

## 浏览记录 📜

每次 `goto` 自动记录到 `logs/YYYY-MM-DD/history.md`：

```markdown
# 🧭 浏览记录 — 2026-06-13

| # | 时间 | 标题 | URL |
|---|------|------|-----|
| 1 | 00:15 | Hacker News | https://news.ycombinator.com |
| 2 | 00:18 | Example Domain | https://example.com |
```

## Do 模式（链式执行）

### 条件等待 ⏳

`wait` 支持多种条件等待，在 do 链中用法：

```json
// 等待文字出现
{"action": "wait", "args": [{"kind": "text", "value": "加载完成"}]}

// 等待 JS 条件成立
{"action": "wait", "args": [{"kind": "js", "value": "document.readyState === 'complete'"}]}

// 等待网络空闲（SPA 数据加载完毕）
{"action": "wait", "args": [{"kind": "network_idle"}]}

// 等待标题变化（可指定值或不指定等待任意变化）
{"action": "wait", "args": [{"kind": "title", "value": "控制台"}]}

// 等待 URL 匹配
{"action": "wait", "args": [{"kind": "url", "value": "/dashboard"}]}

// 自定义超时（默认 30s）
{"action": "wait", "args": [{"kind": "text", "value": "确认", "timeout": 15000}]}
```

### Do 模式

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
# 启动（后台运行，自动启动 Chrome 如未运行）
py scripts/agent_browser.py watch

# 发送命令（写入 JSON 到 cmd.json）
{"action": "goto", "args": ["https://example.com"]}
{"action": "state"}
{"action": "click", "args": ["3"]}
{"action": "exit"}
```

响应写入 `resp.json`。

## 状态文件

- `scripts/runtime/state.json` — 当前交互元素列表（state 命令更新）
- `scripts/runtime/chrome.pid` — Chrome 进程 PID
- `scripts/runtime/watch.pid` — watch 守护进程 PID
- `screenshots/` — 截图存档（命名：YYYYMMDD_HHMMSS_网站名.jpg）
- `downloads/` — 下载文件和 PDF 保存目录
- `user_data/` — Chrome profile（cookie/session 持久化）
- `logs/YYYY-MM-DD/commands.jsonl` — 每日指令日志
- `logs/YYYY-MM-DD/history.md` — 每日浏览记录

## 特殊动作

| 动作 | 说明 |
|------|------|
| `download <url> [save_path]` | 下载文件（直接 URL 或 CSS 选择器触发） |
| `pdf_save [url] [path]` | 保存 PDF 到本地（绕过 Chrome 内置 PDF 查看器） |
| `manual <message> [timeout_s]` | 暂停等待人工介入（默认 600s 超时），完成后创建 `.manual_done` |
| `mouse_click <x> <y>` | 绝对坐标点击 |
| `eval_iframe <js>` | 在第一个 iframe 内执行 JS |

## 登录处理

遇到 SSO/验证码时：

1. `py scripts/agent_browser.py start` 启动 Chrome
2. 在 Chrome 窗口中手动登录
3. Cookie 自动保存到 `user_data/`，后续操作复用登录态

Chrome 关闭后 cookie 依然保留（持久化 profile）。

## 与 v1.x 的核心区别

- Chrome 作为**独立进程**运行，不随 Python 进程绑定
- 即使 Python 进程被超时杀死，Chrome 完好无损，user_data 永不损坏
- 通过 `connect_over_cdp` 连接，不再使用 `launch_persistent_context`

## 书签 📑

v2.0.4 新增书签功能，存储常用网站方便快速调用：

| 命令 | 说明 |
|------|------|
| `bookmarks` | 列出所有书签 |
| `bookmarks add <名称> <URL> [描述]` | 添加书签 |
| `bookmarks search <关键词>` | 搜索书签 |
| `bookmarks remove <序号>` | 删除书签 |

书签文件：`skills/agent-browser/bookmarks.json`（可手动编辑）

## 安全

- 不访问 file:// / localhost / 内网地址
- 密码字段内容不记录日志
- `user_data/` 和 `logs/` 已在 .gitignore 中

## 版本历史

- **v2.1.5** (2026-06-13): `md` 命令、结构化提取、浏览记录、条件等待（text/js/network_idle/title/url）
- **v2.1.4** (2026-06-11): do 链错误恢复（on_error: stop/skip/retry/fallback）
- **v2.1.1** (2026-06-11): 新增 `pdf_save` 命令 + Bug 修复（`FileReader` 替代 `btoa`）
- **v2.1.0**: 重构为 runtime/ 目录
- **v2.0.x**: CDP 独立进程架构、bookmarks、截图优化
- 详见 git log
