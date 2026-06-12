# Agent Browser 待开发清单

> 记录于 v2.1.1 发布后 (2026-06-11 03:50)

## 🔴 P0 — 核心体验

### wait_for 条件等待
消灭盲等 `wait 2000`，支持：
- `wait_for "selector"` → 等到元素出现
- `wait_for "selector" --hidden` → 等到元素消失（loading spinner）
- `wait_for text "加载完成"` → 等到页面包含某文本

### 失败自动截图
`do` 链某步失败时自动截图，debug 不用猜页面状态。

---

## 🟡 P1 — 高频场景

### 结构化提取
- `extract table` → JSON 数组
- `extract table.csv` → CSV
- `extract list` → 字符串数组

### 多字段表单填充
- `fill '{"#user":"name","#pass":"pwd"}'` 一步填完整个表单

---

## 🟢 P2 — 锦上添花

### retry / fallback
- do 链步级自动重试
- 失败切备选方案

### 文件上传
- `upload "#file-input" "C:\path\to\file.pdf"`

### 键盘组合键
- `press Ctrl+A` / `press Ctrl+S`

### 控制台日志
- `console` 命令抓 browser console errors/warnings

### 多标签页协同 do
- 跨标签页链式操作

### Headless 模式
- 对服务器/CI 场景友好
