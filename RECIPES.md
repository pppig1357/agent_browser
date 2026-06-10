# Agent Browser 实战菜谱 🍳

> 从「我的课件的在哪」到「帮我看看最近游戏圈发生了什么」，这里是一切你可以指挥 agent-browser 替你干的事。

---

## 📋 场景总览

| 场景 | 适合 | 难度 |
|------|------|------|
| [学校全家桶：SSO → 下载课件](#场景一学校全家桶sso--下载课件) | 有 SSO 登录的学校/公司系统 | ⭐⭐ |
| [网上冲浪：游戏资讯聚合](#场景二网上冲浪游戏资讯聚合) | 让 AI 替你看网页捞新闻 | ⭐ |
| [邮箱审阅：批量翻未读邮件](#场景三邮箱审阅批量翻未读邮件) | OWA/网页邮箱 | ⭐⭐⭐ |
| [iframe 套娃页面](#场景四iframe-套娃页面) | ehall、老后台系统 | ⭐⭐ |
| [断点续跑：链炸了不重来](#场景五断点续跑链炸了不重来) | 长链容灾 | ⭐ |
| [长时间挂着不怕超时](#场景六长时间挂着不怕超时) | 后台常驻、watch 模式 | ⭐⭐ |
| [PDF 另存为：绕过内置查看器](#场景七pdf-另存为绕过内置查看器) | Chrome 打开 PDF 后无法交互 | ⭐ |

---

## 场景一：学校全家桶（SSO → 下载课件）

> 2026-06-06 实战：XJTLU LMO → CCT008 Quiz.rar（265KB，一遍过）

**你面对的是什么：**

```
打开浏览器 → ehall → Unified ID 登录 → SAML 跳转 → LMO 首页 
→ 点进课程 → 找到资源页 → 找到 .rar 链接 → 下载
```

七步。每一步都可能因为超时、弹窗、重定向崩掉。而 agent-browser 把这七步压进一个 `plan.json`，一次跑完。

**步骤：**

1. 启动 Chrome，在弹出的窗口里手动登录 SSO（cookie 会留下）
```bash
py scripts/agent_browser.py start
py scripts/agent_browser.py manual "请完成 SSO 登录" 600
```

2. 登录成功后，跑下载链：
```bash
py scripts/agent_browser.py do plan.json
```

**plan.json（XJTLU 真实案例）：**
```json
[
  {"action": "goto", "args": ["https://core.xjtlu.edu.cn/my/"]},
  {"action": "wait", "args": ["8000"]},
  {"action": "goto", "args": ["https://core.xjtlu.edu.cn/course/view.php?id=xxxxx"]},
  {"action": "wait", "args": ["3000"]},
  {"action": "download", "args": ["a[href*='.rar']", "./downloads/Quiz_CCT008.rar"]}
]
```

**为什么它可靠：**
- `goto` 直达 URL 比模拟点击稳一万倍
- `download` 用 CSS 选择器匹配 `.rar` 链接，不怕页面改版
- `wait` 给 SPA 渲染留缓冲（LMO 的 JS 加载很慢）
- SSO cookie 在 `user_data/` 里持续有效，第二次跑不需要重新登录

**真实数据：** v1.1.0 时代被 SIGKILL 杀了 4 次才成功，v2.0 一遍过，全程约 4 分钟。

---

## 场景二：网上冲浪（游戏资讯聚合）

> 2026-06-06 实战：游民星空 → 3DM → 游研社 → 机核，四站 5 分钟

**Why agent-browser 而不是 web_fetch：**
- 部分网站有反爬（Cloudflare、人机验证）
- agent-browser 以真实浏览器身份访问，不容易被拦
- 可以看到 JS 渲染后的真实内容（`state` + `eval`）
- 弹窗广告可以直接 js 秒杀

**步骤：**

1. 启动，打开第一个站
```bash
py scripts/agent_browser.py start
py scripts/agent_browser.py goto "https://www.gamersky.com/"
```

2. 用 `eval` 处理弹窗（一巴掌把所有 close 按钮按了）
```bash
py scripts/agent_browser.py eval "document.querySelectorAll('[class*=close],[class*=Close]').forEach(el=>{if(el.offsetParent)el.click()}); 'done'"
```

3. 用 `eval` 提取新闻标题+链接
```bash
py scripts/agent_browser.py eval "JSON.stringify(Array.from(document.querySelectorAll('h3 a,.newsTit a')).slice(0,20).map(a=>({title:a.textContent.trim(),href:a.href})).filter(x=>x.title.length>5))"
```

4. 如法炮制其他站点，数据全喂给 AI 做汇总

**实战经验：**
- 游民星空首页有大量娱乐八卦混在新闻里，需要 `filter(x.title.length > 5)` 淘掉
- 3DM 的新闻区块最干净，`class="newslist"` 直接命中
- 游研社深度文章多，title 经常带 \n 和副标题，需要 trim 处理
- 机核（gcores）前端框架特殊，通用选择器容易空返回，需要遍历全页 `a` 标签按 URL 特征过滤
- 弹窗广告有但不多，一次 eval 全灭

---

## 场景三：邮箱审阅（批量翻未读邮件）

> 2026-06-06 实战：XJTLU OWA (Exchange 2019)，20分钟鏖战，以失败告终 🙃

**为什么 OWA 翻了车：**

Exchange 2019 OWA 是一个**纯 SPA + 阅读窗格默认关闭**的怪物：

| 尝试 | 结果 |
|------|------|
| `click` 邮件行 | 只选中，不打开 ❌ |
| `dblclick` 邮件行 | v2.0.4 新增此命令，但当时还没写 😅 |
| `eval` 注入点击 | OWA 用 React 事件委托，原生 click 无效 ❌ |
| `goto` 邮件 URL | 所有链接都是 `#`，纯 SPA 路由 ❌ |
| OWA Light 版本 | 行为完全一致 ❌ |
| OWA Options 改设置 | 找不到开启阅读窗格的入口 ❌ |
| 截图看邮件列表 | 能用，但 `screenshot` 后面退化了（CDP 连接劣化）⚠️ |

**最终方案：** 手机 QQ 邮箱代收 😂

**为什么 QQ 邮箱可以而 OWA 不行：**
- QQ 邮箱的网页版是传统多页面架构，每封邮件有独立 URL
- OWA 是纯 SPA，所有状态在内存里，URL 不变

**教训：**

如果目标系统是 SPA（页面 URL 不变、内容通过 JS 动态渲染）：
1. 先试试 `dblclick`（v2.0.4 新增）——OWA 的邮件打开就需要双击
2. 如果 dblclick 也不行，试试 `eval` 手动触发 React/Vue 事件
3. 如果所有手段都凉了，看看手机 App 有没有 Web 版

---

## 场景四：iframe 套娃页面

> 在 SPA 外层套 iframe，iframe 里再套 SPA，经典校园系统架构 👍

```json
[
  {"action": "goto", "args": ["https://portal.example.com"]},
  {"action": "wait", "args": ["5000"]},
  {"action": "eval_iframe", "args": ["return document.querySelector('[aria-label*=\"目标入口\"]')?.parentElement?.click();"]},
  {"action": "wait", "args": ["3000"]}
]
```

**要点：**
- `eval_iframe` 专门用于在 iframe 上下文执行 JS
- iframe 内的元素无法通过外层 CSS selector 定位
- 不要试图用 `click "iframe:xxx"` 去点深层嵌套的元素，`eval_iframe` 更可靠

---

## 场景五：断点续跑（链炸了不重来）

```bash
# 第 3 步崩了，从第 3 步接着跑
py scripts/agent_browser.py do plan.json --resume-from=3
```

**适用情况：**
- 10 步以上的长链中间某一步崩了
- 前几步要登录/验证/等接口返回，重跑代价大
- 某一步需要人工介入（`manual`），人工做完后继续

**注意事项：**
- `--resume-from` 的索引从 1 开始（不是 0）！
- 重跑时浏览器状态 = 上一步崩掉时的状态，所以第 N 步的前提条件（页面 URL、DOM 等）需要保持一致

---

## 场景六：长时间挂着不怕超时

```bash
py scripts/agent_browser.py start
py scripts/agent_browser.py watch   # 后台 IPC 模式，通过 cmd.json/resp.json 通信
# 即使 watch 被超时杀了，Chrome 独立进程完好
# 再开一个 watch 继续用
```

**适用情况：**
- 需要在好几个小时内随时操控浏览器
- AI agent 通过文件 IPC（`cmd.json` → `resp.json`）间歇性发指令
- 不想每次操作都重新连接 CDP

---

## 📑 书签：常用网站速查

```bash
py scripts/agent_browser.py bookmarks                  # 列出所有书签
py scripts/agent_browser.py bookmarks add "3DM" URL    # 添加
py scripts/agent_browser.py bookmarks search "学校"    # 搜索
```

---

## 场景七：PDF 另存为（绕过内置查看器）

> 2026-06-11 实战：LMO → EAP045 Module Handbook（766KB），SSO 认证直链一次保存成功

**你面对的是什么：**

Chrome 自带 PDF 查看器（`chrome-extension://` 或 `pluginfile.php` 直链），页面上的 PDF 直接在浏览器里预览——但脚本对它无能为力！`state` 看不到 PDF 内容，`click` 点不了里面的按钮，`extract` 拿不到文字。

**`pdf_save` 是如何绕过的：**

不跟 PDF 查看器较劲，直接走 JS `fetch()` 拿原始 PDF 字节，通过 `FileReader.readAsDataURL` 转 base64，Python 解码写本地文件。

**实战步骤：**

1. 先找到 PDF 链接（Moodle 的 `pluginfile.php` 直链藏在 resource 页面里）
```bash
py agent_browser.py start
py agent_browser.py goto https://core.xjtlu.edu.cn/course/view.php?id=5498
py agent_browser.py eval "Array.from(document.querySelectorAll('a')).filter(function(a){return a.href.includes('.pdf')}).map(function(a){return a.href})"
# → ["https://core.xjtlu.edu.cn/pluginfile.php/139292/..."]
```

2. 一个命令保存到本地
```bash
py agent_browser.py pdf_save "https://core.xjtlu.edu.cn/pluginfile.php/139292/mod_resource/content/1/EAP045%20Module%20Handbook%20Semester%202%202025-26.pdf"
# → 📄 PDF 已保存: downloads/EAP045 Module Handbook Semester 2 2025-26.pdf (766,880 bytes)
```

3. 或者直接保存当前页面（如果你已经导航到了 PDF）
```bash
py agent_browser.py pdf_save
```

**实现细节：**
- 初版用 `btoa` + `TextDecoder('latin1')` 对大 PDF 不稳定（非 ASCII 字节导致 `btoa` 抛异常）
- 终版用 `FileReader.readAsDataURL` — 浏览器原生 base64，零字节损坏，稳如老狗
- `pdf_save` 在 `do` 模式下也能用：`{"action":"pdf_save","args":["https://...pdf"]}`

**注意事项：**
- PDF URL 如果需要 cookie/SSO 认证，必须在同一 CDP 会话里 `pdf_save`（当前正在用的浏览器窗口里有 session）
- 大 PDF（>50MB）可能因为 base64 串过大导致 JS 内存压力，但学校课件级别的 PDF 毫无压力

---

*More recipes coming as we break more things 🦞*
