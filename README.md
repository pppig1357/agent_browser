# Agent Browser v1.1.0

基于 Playwright 的持久化浏览器工具，为 AI agent 提供可靠的浏览器自动化操作。

## 设计理念

- **一次登录，永久使用** — 使用 `launch_persistent_context`，cookie 跨会话持久化
- **链式操作为主，watch 为辅** — `do` 模式在同一 session 中跑完整条链，比 watch IPC 模式更可靠
- **优雅降级** — click 有 3 级 fallback（Playwright → JS → href 导航），不怕 selector 环境差异
- **最小依赖** — 只依赖 `playwright`，Python 标准库处理其余所有事情

## 首次使用

1. 安装依赖：
```bash
pip install playwright
playwright install chromium
```

2. 启动 watch 模式，弹出 Chrome 窗口：
```bash
py scripts/agent_browser.py watch
```

3. 在 Chrome 窗口中手动登录目标网站（SSO 等）
4. Cookie 自动持久化到 `user_data/`，后续操作复用登录态
5. 测试是否正常：
```bash
py scripts/agent_browser.py goto https://example.com
py scripts/agent_browser.py state
```

> 💡 `user_data/` 和 `logs/` 已加入 `.gitignore`，不会上传到仓库。首次运行时自动创建。

## 快速开始

```bash
# 安装依赖（如已完成可跳过）
pip install playwright
playwright install chromium

# 测试
py scripts/agent_browser.py goto https://example.com
py scripts/agent_browser.py state
py scripts/agent_browser.py click 1
```

## 典型场景

### 场景一：SSO 登录 → LMS 下载课件

```json
[
  {"action": "goto", "args": ["https://lms.example.edu/login"]},
  {"action": "click", "args": ["1"]},
  {"action": "manual", "args": ["请完成 SSO 登录"]},
  {"action": "goto", "args": ["https://lms.example.edu/my/courses"]},
  {"action": "state"},
  {"action": "eval", "args": ["// 搜索目标课程链接"]},
  {"action": "goto", "args": ["https://lms.example.edu/mod/resource/view.php?id=xxx"]},
  {"action": "download", "args": ["a[href*='.rar']", "./downloads/quiz.rar"]}
]
```

### 场景二：iframe SPA 内操作

```json
[
  {"action": "goto", "args": ["https://portal.example.com"]},
  {"action": "wait", "args": ["5000"]},
  {"action": "eval_iframe", "args": ["return document.querySelector('[aria-label*=\"课程中心\"]')?.parentElement?.click();"]},
  {"action": "wait", "args": ["3000"]}
]
```

### 场景三：断点续跑

```bash
# 第 3 步炸了？修好 plan.json 后从第 3 步继续
py scripts/agent_browser.py do plan.json --resume-from=2
```

## 经验教训

### ✅ 该做的

- **用 `do` 模式，不要用单独命令循环** — 每关一次浏览器就可能丢 cookie
- **直接导航 URL 优于模拟点击** — 如果知道目标 URL，`goto` 永远比 `click + wait` 可靠
- **用 `eval` 先探路再写 plan** — 盲写 click 索引是赌博
- **做好 `--resume-from` 预案** — 长链一定会炸

### ❌ 别做的

- 别在 loop 里反复打开/关闭浏览器 — 单次 `do` 链完成所有事
- 别依赖 CSS class 顺序 — Moodle/Canvas 等 LMS 换皮肤时 class 会变
- 别用 `headless=True` 做复杂交互 — SSR 页面在 headless 下表现不同
- 别把 `download` 写到单独脚本 — 必须用同一个 browser context 才能复用 session

## 命令速查

| 单步命令 | `do` 模式 JSON | 说明 |
|----------|---------------|------|
| `goto URL` | `{"action":"goto","args":["URL"]}` | 打开网址 |
| `state` | `{"action":"state"}` | 列出元素 |
| `click 3` | `{"action":"click","args":["3"]}` | 按索引点击 |
| `click ".btn"` | `{"action":"click","args":[".btn"]}` | CSS 点击 |
| `click ".link" --wait-nav` | `{"action":"click","args":[".link","--wait-nav"]}` | 点击等导航 |
| `eval JS` | `{"action":"eval","args":["JS"]}` | 执行 JS |
| `download URL PATH` | `{"action":"download","args":["URL","PATH"]}` | 下载文件 |
| `manual message` | `{"action":"manual","args":["message"]}` | 人工介入 |
| `wait 3000` | `{"action":"wait","args":["3000"]}` | 等待 ms |

## 架构

```
skills/agent-browser/
├── SKILL.md           # OpenClaw skill 定义
├── README.md          # 本文档
├── scripts/
│   ├── agent_browser.py  # 主程序
│   ├── state.json        # 最后的状态快照
│   └── watch.pid         # watch 进程 PID
├── user_data/            # Chrome profile (持久化)
│   └── Default/
│       ├── Cookies
│       ├── Local Storage/
│       └── ...
└── logs/
    └── YYYY-MM-DD/
        └── commands.jsonl
```

## 版本历史

### v1.1.0 (2026-06-05)
- 修复 CSS selector 生成器 trailing dot bug（`filter(Boolean)`）
- 修复空 `id` 属性产生非法 `#` 选择器
- click-by-index 三级降级：Playwright → JS → href 导航
- 新: `download` action（直接 URL 或 CSS 选择器触发下载）
- 新: `do` 模式支持 `--resume-from=N` 断点续跑
- 新: `click` 支持 `--wait-nav` 等待 SPA 页面跳转

### v1.0.0 (2026-06-05)
- 初始版本：goto / state / click / type / press / screenshot / extract / scroll / eval / tabs / watch / do
