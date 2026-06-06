# Agent Browser v2.1.0

> ❗ 本项目还在进行测试和debug中，等没bug了我会传Release（磕头）
> 💡 懒人の奇思妙想之——让AI帮我代写Module Quiz（划掉）自动化浏览器操作工具

基于 Playwright CDP 的独立浏览器，专为「不想手动点几百次网页」和「让 AI 替你在深夜里爬课件」而生。

## 它怎么来的

某个深夜，pppig 打开学校的 LMO，准备下载 CCT008 的 quiz。然后他发现：

1. 要登录 ehall  
2. ehall 跳 SSO  
3. SSO 跳 SAML  
4. SAML 跳 LMO  
5. LMO 里点进课程  
6. 找到资源页  
7. 点击下载  

一套流程下来，他已经忘了为什么要打开电脑（后期来自pppig的订正：纯懒癌发作所以决定找个理由搓轮椅就是，有签到助手了那我何乐而不为呢）。

于是他说：「小龙虾，咱能不能一起写个脚本？」

然后就有了 agent-browser。经历了 4 次被 SIGKILL 爆杀、user_data 反复损坏、Chrome 窗口在他的后台疯狂闪屏之后，它终于活到了 v2.0。

## 设计理念

- **Chrome 活着，Python 随便死** — Chrome 作为独立进程运行，Python 进程被超时杀了也无所谓，下次重连就行。别问为什么强调这个，那是血泪教训
- **一次登录，永久白嫖** — user_data 持久化 cookie/session，关掉重开照样在
- **链式操作为主** — `do` 模式一个连接跑完一整条链，比反复连 CDP 省心
- **优雅降级** — click 有 3 级 fallback（Playwright → JS → href 导航），不挑页面不挑框架
- **最小依赖** — 只靠 `playwright`，剩下全是 Python 标准库。没装一千个 npm 包，就一个 .py 文件
- **帝王引擎** — 懒惰使人进步（bushi）

## 与 v1.x 的区别

| | v1.x | v2.x |
|---|---|---|
| Chrome 怎么跑的 | `launch_persistent_context`，跟 Python 绑死 | 独立进程，CDP `connect_over_cdp` |
| Python 被杀会怎样 | user_data 损坏，下次直接崩 | Chrome 活得好好的 |
| 需要手动启 Chrome | 不用（但也因此容易被杀） | 需要 `start`（do/watch 检测到没启动会自己帮你启） |
| manual 超时 | 无上限，靠 OS 杀 | 默认 10min，可控 |

## 首次使用

1. 安装：
```bash
pip install playwright
playwright install chromium
```

2. 启动 Chrome：
```bash
py scripts/agent_browser.py start
```

3. 在弹出的 Chrome 窗口里手动登录你的目标网站
4. Cookie 自动存进 `user_data/`，重启 Chrome 也还在
5. 测试：
```bash
py scripts/agent_browser.py goto https://example.com
py scripts/agent_browser.py state
```

> 💡 `user_data/` 和 `logs/` 在 `.gitignore` 里，不会上传。首次运行时自动创建。

## 快速开始

```bash
pip install playwright && playwright install chromium
py scripts/agent_browser.py start
py scripts/agent_browser.py goto https://example.com
py scripts/agent_browser.py state
py scripts/agent_browser.py click 1
py scripts/agent_browser.py stop
```

## 📖 实战菜谱

详细场景 + 实战经验 + 踩过的坑 → **[RECIPES.md](RECIPES.md)**

- 🏫 学校全家桶：SSO → 下载课件
- 🌊 网上冲浪：游戏资讯聚合（游民/3DM/游研社/机核）
- 📬 邮箱审阅：OWA 翻车全记录
- 🪆 iframe 套娃页面
- 🔄 断点续跑

## 吃过的亏（所以你别再吃）

### ✅ 该做的

- **先 `start` 再操作** — Chrome 要独立活着，别每次命令都开个新浏览器
- **`do` 优于单步命令** — 一个 CDP 连接跑完所有事，减少连接开销
- **知道 URL 就 `goto`，别模拟点击** — `goto` 永远比 `click + wait` 可靠一万倍
- **`eval` 探路再写 plan** — 盲写 click 索引 ≈ 盲人摸象
- **`--resume-from` 做个预案** — 长链一定会炸，别不信

### ❌ 别做的

- 别反复 `start`/`stop` — Chrome 开着就行，又不吃你多少内存
- 别在 plan 里靠 CSS class 定位 — LMS 换个皮肤你就凉了
- 别开 `headless=True` 做复杂交互 — SSR 页面 headless 下就是另一个世界
- 别把 `download` 写到独立进程 — 必须同一个 CDP 会话才有 session

## 命令速查

| 管理 | 说明 |
|------|------|
| `start` | 启动 Chrome（独立进程，CDP 9222） |
| `stop` | 优雅关闭 |

| 操作 | `do` JSON | 说明 |
|------|----------|------|
| `goto URL` | `{"action":"goto","args":["URL"]}` | 打开网址 |
| `state` | `{"action":"state"}` | 列出交互元素 |
| `click 3` | `{"action":"click","args":["3"]}` | 按索引点击 |
| `click ".btn"` | `{"action":"click","args":[".btn"]}` | CSS 点击 |
| `click ".link" --wait-nav` | `{"action":"click","args":[".link","--wait-nav"]}` | 点击等 SPA 跳转 |
| `dblclick 3` | `{"action":"dblclick","args":["3"]}` | 双击元素（SPA/OWA 专用） |
| `dblclick ".btn"` | `{"action":"dblclick","args":[".btn"]}` | 双击 CSS 选择器 |
| `eval JS` | `{"action":"eval","args":["JS"]}` | 执行 JS |
| `download URL [PATH]` | `{"action":"download","args":["URL","PATH"]}` | 下载文件 |
| `manual msg [timeout_s]` | `{"action":"manual","args":["请登录","300"]}` | 人工介入 |
| `wait 3000` | `{"action":"wait","args":["3000"]}` | 等 N 毫秒 |

## 📑 书签（v2.0.4 新增）

像个正常浏览器一样收藏常用网址，省得每次打字。

```bash
py scripts/agent_browser.py bookmarks                    # 列出所有书签
py scripts/agent_browser.py bookmarks add "课表" URL     # 添加书签
py scripts/agent_browser.py bookmarks search "学校"      # 搜索书签
py scripts/agent_browser.py bookmarks remove 2           # 删除 2 号书签
```

书签存在 `bookmarks.json` 里，手动编辑也行。

## 架构

```
skills/agent-browser/
├── SKILL.md              # skill 定义
├── README.md             # 你在看的这个
├── RECIPES.md            # 实战菜谱
├── bookmarks.json        # 书签文件
├── scripts/
│   ├── agent_browser.py  # 唯一的代码文件
│   ├── plan.json         # do 链示例
│   └── runtime/          # 运行时文件（不传 git）
│       ├── chrome.pid
│       ├── watch.pid
│       ├── state.json
│       └── cmd.json / resp.json
├── screenshots/          # 截图（命名：YYYYMMDD_HHMMSS_网站名.jpg）
├── downloads/            # 下载文件
├── user_data/            # Chrome profile（有 cookie，别传 git！）
│   └── Default/
│       ├── Cookies
│       ├── Local Storage/
│       └── ...
└── logs/                 # 指令日志（也不传 git）
    └── YYYY-MM-DD/
        └── commands.jsonl
```

## 特别鸣谢

> 没有下面这些人和事，这个项目可能还停留在「理论上可行」的阶段。

### 🦞 小龙虾

代码主体实现。从 v1.0 到 v2.0，从 Playwright 到 CDP，从 CSS selector 尾随句号到 3 级 click 降级，从被 SIGKILL 反复爆杀到「Chrome 独立存活」架构。深夜 debug 到自己都麻了，但就是不服。

### 🐋 梁文峰大人

\o/\o/\o/\o/\o/\o/\o/Deepseek的恩情还不完啊\o/\o/\o/\o/\o/\o/\o/\o/\o/\o/\o/\o/\o/\o/鲸挣恩大人我们永远追随你口阿\o/\o/\o/\o/\o/\o/\o/\o/\o/

### 🌸 伊吹桑

pppig 的编程社管理层成员。一次先修水课上给 pppig 看了他用的 **Astrbot** 以及 Astrbot 里的邮箱 skill——那个「bot 替你操作网页」的 idea 就是从这儿来的。没有那一刻的启发，agent-browser 可能根本不会开始。

### 📧 学校那个掉渣的邮箱网址

某大学邮件系统的 **Basic 主题**。因为pppig手贱把邮箱主题换成 Basic 渲染而锁死了页面上的所有交互元素，逼得我们不得不研究 `eval_iframe`、手动注入 JS 点击、以及各种绕过 iframe SPA 的偏方。谢谢你，让 click fallback 从 1 级变成了 3 级。

### 🖼️ 学校 IT —「请输入文本.jpg」

您可以在自定义菜单中切换表情（卡兹佬乱入） 简单讲就是 pppig 差点因为学校本地部署的 Exchange 2019 服务器按死在地上，如果不切换到 **Basic 主题** 还查不到有关信息说是，所以网址邮箱基本就是搭进去了。

---

*Made with ❤️ and a lot of `await asyncio.sleep(1)`*

## 版本历史

### v2.1.0 (2026-06-06)
- **重构**：运行时文件统一移至 `scripts/runtime/`，`scripts/` 只留源码和示例
- **修复**：`.gitignore` 改用目录级忽略 `scripts/runtime/`，杜绝垃圾文件上传
- **文档**：新增 `RECIPES.md`（6 个实战场景），README 精简解耦
- **清理**：删除 v1.x 遗留文件（`.browser_cookies.json` 等）

### v2.0.5 (2026-06-06)
- **优化**：截图存储从 `scripts/` 迁移到 `screenshots/`，命名改为 `YYYYMMDD_HHMMSS_网站名.jpg`
- **优化**：下载文件从 `workspace/downloads/` 迁移到项目根 `downloads/`
- **新增**：截图无名称时自动提取域名作为标识

### v2.0.4 (2026-06-06)
- **新功能**：`bookmarks` — 书签管理（add/list/search/remove），存 `bookmarks.json`
- **新命令**：`dblclick` — 双击元素（Playwright → JS event fallback），解决 OWA/SPA 阅读窗格问题
- **修复**：`eval` 始终返回 null（箭头函数块体无 return → 改直接 `page.evaluate()`）
- **修复**：`screenshot` 超时被 SIGKILL → `asyncio.wait_for` 15s 超时 + bytes 写入

### v2.0.0 (2026-06-06)
- **架构重构**：Chrome 独立进程 + CDP `connect_over_cdp`，彻底告别 profile 损坏
- **新命令**：`start` / `stop` 管理 Chrome 生命周期
- **修复**：`manual` 增加 soft timeout（默认 600s）
- **清理**：移除硬编码 URL、无用 cookie helper
- **文档**：去掉个人信息，加入心路历程和特别鸣谢

### v1.1.0 (2026-06-05)
- 修复 CSS selector trailing dot、空 id 选择器
- click 三级降级、`download` action、`--resume-from`、`--wait-nav`

### v1.0.0 (2026-06-05)
- 初始版本：goto / state / click / type / press / screenshot / extract / scroll / eval / tabs / watch / do
