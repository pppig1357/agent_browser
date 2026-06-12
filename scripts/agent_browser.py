#!/usr/bin/env py
# -*- coding: utf-8 -*-
"""
Agent Browser v2.1.5 — AI-controllable browser via CDP
Usage:
  py agent_browser.py start                # Launch independent Chrome
  py agent_browser.py stop                 # Gracefully close Chrome
  py agent_browser.py goto <url>
  py agent_browser.py state
  py agent_browser.py click <n|selector>
  py agent_browser.py type <text>          # into focused element
  py agent_browser.py type <selector> <text>
  py agent_browser.py press <key>
  py agent_browser.py screenshot [name]
  py agent_browser.py extract [selector]   # plain text or structured (JSON config)
  py agent_browser.py md [save_path]       # convert page to markdown
  py agent_browser.py html [selector]
  py agent_browser.py scroll <up|down> [px]
  py agent_browser.py wait <ms|selector>               # legacy
  py agent_browser.py wait --text <text>                # wait until text appears
  py agent_browser.py wait --js <expression>            # wait for JS condition
  py agent_browser.py wait --network-idle               # wait for network idle
  py agent_browser.py wait --title <text>               # wait for title
  py agent_browser.py wait --url <pattern>              # wait for URL match
  py agent_browser.py eval <js>
  py agent_browser.py tabs [list|switch <n>|new|close]
  py agent_browser.py pdf_save [url] [save_path]  # Save PDF to local
  py agent_browser.py watch                # daemon mode (file IPC)
  py agent_browser.py do <plan.json> [--resume-from=N]  # do chain with error recovery

Architecture:
  Chrome runs as an independent process (start/stop).
  All other commands connect via CDP — process killed ≠ Chrome killed.
  user_data/ persists cookies across sessions.
  log/YYYY-MM-DD/history.md tracks browsing history.
"""

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

SCRIPT_DIR = Path(__file__).parent
SKILL_DIR = SCRIPT_DIR.parent  # skills/agent-browser/
RUNTIME_DIR = SCRIPT_DIR / "runtime"  # v2.1.0: runtime files live here
USER_DATA = SKILL_DIR / "user_data"
SCREENSHOTS_DIR = SKILL_DIR / "screenshots"
DOWNLOADS_DIR = SKILL_DIR / "downloads"
STATE_FILE = RUNTIME_DIR / "state.json"
BOOKMARKS_FILE = SKILL_DIR / "bookmarks.json"
LOG_DIR = SKILL_DIR / "logs"
CHROME_EXE_CACHE = RUNTIME_DIR / ".chrome_exe_path"
PID_FILE = RUNTIME_DIR / "chrome.pid"
CDP_PORT = 9222
CDP_URL = f"http://localhost:{CDP_PORT}"

USER_DATA.mkdir(parents=True, exist_ok=True)
RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)


# ────────────────── helpers ──────────────────

def _log_dir():
    d = LOG_DIR / datetime.now().strftime("%Y-%m-%d")
    d.mkdir(exist_ok=True)
    return d


async def _log_cmd(action, args, result):
    """Append to commands.jsonl in daily log dir."""
    try:
        entry = {
            "time": datetime.now().strftime("%H:%M:%S.%f")[:-3],
            "action": action,
            "args": str(args)[:500],
            "ok": result.get("ok", True),
            "summary": str(result.get("summary", ""))[:300]
        }
        ldir = _log_dir()
        with open(ldir / "commands.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _history_file():
    """Ensure log/YYYY-MM-DD/history.md exists, return Path."""
    d = _log_dir()
    p = d / "history.md"
    if not p.exists():
        today = datetime.now().strftime("%Y-%m-%d")
        p.write_text(
            f"# 🧭 浏览记录 — {today}\n\n"
            f"| # | 时间 | 标题 | URL |\n"
            f"|---|------|------|-----|\n",
            encoding="utf-8")
    return p


def _append_history(url, title, screenshot_path=None):
    """Append a row to today's history.md."""
    try:
        p = _history_file()
        now = datetime.now().strftime("%H:%M:%S")
        # Count existing rows (skip header lines)
        lines = p.read_text(encoding="utf-8").strip().split("\n")
        row_count = sum(1 for l in lines if l.startswith("|") and not l.startswith("|---") and "标题" not in l)
        idx = row_count + 1
        # Truncate title for table
        short_title = title[:60].replace("|", "\\|").replace("\n", " ")
        ss_cell = f"[📸]({screenshot_path.replace(chr(92), '/')})" if screenshot_path else "—"
        row = f"| {idx} | {now} | {short_title} | {url} |\n"
        with open(p, "a", encoding="utf-8") as f:
            f.write(row)
    except Exception:
        pass


# ── v2.1.5: page-to-markdown JS (injected via CDP) ──

_PAGE_TO_MD_JS = r"""
(() => {
  const SKIP = new Set(['SCRIPT','STYLE','NOSCRIPT','IFRAME','SVG','NAV','FOOTER','HEADER','META','LINK']);
  const BLOCK = new Set(['P','DIV','SECTION','ARTICLE','MAIN','ASIDE','LI','TD','TH','BLOCKQUOTE','PRE','H1','H2','H3','H4','H5','H6','UL','OL','TABLE','HR','FIGURE','FIGCAPTION','DETAILS','SUMMARY','FORM','FIELDSET']);
  let out = '';
  const seen = new Set();

  function walk(el, depth) {
    if (!el || depth > 30) return;
    if (el.nodeType === 3) {
      const t = el.textContent.replace(/\s+/g, ' ');
      if (t && t !== ' ') out += t;
      return;
    }
    if (el.nodeType !== 1) return;
    if (seen.has(el)) return;
    seen.add(el);
    const tag = el.tagName;
    if (SKIP.has(tag)) return;

    // Inline formatting
    if (tag === 'A') {
      const href = el.getAttribute('href') || '';
      const abs = href && (href.startsWith('http') || href.startsWith('/')) ? href : '';
      const prev = out.length;
      for (const c of el.childNodes) walk(c, depth+1);
      const text = out.slice(prev).trim();
      out = out.slice(0, prev);
      if (text && abs) out += '[' + text + '](' + abs + ')';
      else if (text) out += text;
      return;
    }
    if (tag === 'STRONG' || tag === 'B') { out += '**'; for (const c of el.childNodes) walk(c, depth+1); out += '**'; return; }
    if (tag === 'EM' || tag === 'I') { out += '*'; for (const c of el.childNodes) walk(c, depth+1); out += '*'; return; }
    if (tag === 'CODE' && !BLOCK.has(el.parentElement?.tagName||'')) { out += '`'; for (const c of el.childNodes) walk(c, depth+1); out += '`'; return; }
    if (tag === 'IMG') { const src=el.getAttribute('src')||'', alt=el.getAttribute('alt')||''; if(src) out += '\n!['+alt+']('+src+')\n'; return; }
    if (tag === 'BR') { out += '\n'; return; }
    if (tag === 'HR') { out += '\n\n---\n\n'; return; }

    // Block elements
    const isBlock = BLOCK.has(tag);
    if (isBlock) {
      const before = out.length;
      const tagBefore = out;
      for (const c of el.childNodes) walk(c, depth+1);
      let content = out.slice(before).trim();
      // Remove nested block markers
      content = content.replace(/\n{3,}/g, '\n\n');
      if (!content) return;

      if (tag === 'H1') out = out.slice(0, before) + '\n\n# ' + content + '\n';
      else if (tag === 'H2') out = out.slice(0, before) + '\n\n## ' + content + '\n';
      else if (tag === 'H3') out = out.slice(0, before) + '\n\n### ' + content + '\n';
      else if (tag === 'H4') out = out.slice(0, before) + '\n\n#### ' + content + '\n';
      else if (tag === 'H5') out = out.slice(0, before) + '\n\n##### ' + content + '\n';
      else if (tag === 'H6') out = out.slice(0, before) + '\n\n###### ' + content + '\n';
      else if (tag === 'LI') out = out.slice(0, before) + '- ' + content + '\n';
      else if (tag === 'BLOCKQUOTE') out = out.slice(0, before) + '\n> ' + content.replace(/\n/g, '\n> ') + '\n';
      else if (tag === 'PRE') out = out.slice(0, before) + '\n```\n' + (el.textContent||'').trim() + '\n```\n';
      else out = out.slice(0, before) + '\n\n' + content + '\n';
    } else {
      for (const c of el.childNodes) walk(c, depth+1);
    }
  }

  // Start from main content area or body
  const root = document.querySelector('article, main, [role="main"]') || document.body;
  walk(root, 0);
  // Post-process: collapse blank lines
  return out.replace(/\n{3,}/g, '\n\n').trim();
})()
"""


def _check_chrome_running():
    """Quick sync check: is Chrome CDP reachable?"""
    try:
        import urllib.request
        urllib.request.urlopen(f"{CDP_URL}/json/version", timeout=2)
        return True
    except Exception:
        return False


async def _get_chrome_exe():
    """Find Chromium path (cached)."""
    if CHROME_EXE_CACHE.exists():
        exe = CHROME_EXE_CACHE.read_text().strip()
        if Path(exe).exists():
            return exe
    # Use Playwright to locate
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        exe = str(p.chromium.executable_path)
    CHROME_EXE_CACHE.write_text(exe)
    return exe


async def _connect(p):
    """Connect to running Chrome via CDP. Returns (browser, page)."""
    browser = await p.chromium.connect_over_cdp(CDP_URL)
    contexts = browser.contexts
    if contexts:
        ctx = contexts[0]
        if ctx.pages:
            page = ctx.pages[0]
        else:
            page = await ctx.new_page()
    else:
        ctx = await browser.new_context(
            viewport={"width": 1280, "height": 800}, locale="zh-CN"
        )
        page = await ctx.new_page()
    return browser, page


# ────────────────── browser state extraction ──────────────────

async def cmd_state(page):
    """List interactive elements with indices."""
    elements = await page.evaluate("""() => {
        const items = [];
        const selectors = 'a, button, input, select, textarea, [role="button"], [onclick], summary, details, [tabindex]';
        const els = document.querySelectorAll(selectors);
        let idx = 0;
        for (const el of els) {
            if (idx >= 100) break;
            const rect = el.getBoundingClientRect();
            if (rect.width === 0 || rect.height === 0) continue;
            const tag = el.tagName.toLowerCase();
            const type = el.getAttribute('type') || '';
            const id = el.id ? '#' + el.id : '';
            const cls = el.className && typeof el.className === 'string'
                ? '.' + el.className.split(/\\s+/).filter(Boolean).slice(0,2).join('.') : '';
            const sel = tag + id + cls;
            const text = (el.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 60);
            const placeholder = el.getAttribute('placeholder') || '';
            const href = el.getAttribute('href') || '';
            const name = el.getAttribute('name') || '';
            const aria = el.getAttribute('aria-label') || '';
            const label = text || placeholder || aria || href || name || (tag + type);
            items.push({
                i: idx,
                tag: tag + (type ? `[type=${type}]` : ''),
                sel: tag + id + cls,
                label: label,
                placeholder: placeholder,
                href: href.slice(0, 80),
                visible: rect.top < window.innerHeight && rect.bottom > 0
            });
            idx++;
        }
        return items;
    }""")

    state = {
        "url": page.url, "title": await page.title(), "elements": elements,
        "visible": len([e for e in elements if e["visible"]]), "total": len(elements)
    }
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"📍 {state['title']}")
    print(f"🔗 {state['url']}")
    print(f"🧩 {state['visible']}/{state['total']} 交互元素\n")
    for e in elements:
        marker = "👁" if e["visible"] else "⬇"
        print(f"  [{e['i']}] {marker} {e['tag']} | {e['label'][:70]}")
    return {"ok": True, "state": state}


async def cmd_screenshot(page, name=None):
    # v2.0.5: screenshots go to SKILL_DIR/screenshots/, named YYYYMMDD_HHMMSS_sitename.jpg
    from datetime import datetime
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    if name:
        site_label = name
    else:
        try:
            from urllib.parse import urlparse
            host = urlparse(page.url).hostname or "page"
            site_label = host.replace("www.", "").split(".")[0]
        except Exception:
            site_label = "page"
    filename = f"{ts}_{site_label}"
    path = str(SCREENSHOTS_DIR / f"{filename}.jpg")
    print("📸 capturing...", flush=True)
    try:
        data = await asyncio.wait_for(page.screenshot(type="jpeg", quality=85, full_page=False), timeout=15)
        Path(path).write_bytes(data)
        print(f"📸 {path}")
        return {"ok": True, "path": path}
    except asyncio.TimeoutError:
        print("⚠️ Screenshot timed out", flush=True)
        return {"ok": False, "error": "screenshot timed out after 15s"}


async def cmd_extract(page, selector=None):
    if selector:
        try:
            text = await page.locator(selector).first.inner_text()
        except Exception:
            text = ""
        if not text:
            text = await page.evaluate(
                f"""(sel) => {{ const el=document.querySelector(sel);return el?el.textContent.trim():''; }}""",
                selector)
    else:
        text = await page.evaluate("() => document.body.innerText")
    lines = text.strip().split("\n")[:200]
    result = "\n".join(lines)
    print(result[:8000])
    return {"ok": True, "text": result[:2000], "lines": len(lines)}


async def cmd_md(page, save_path=None):
    """v2.1.5: Convert current page to Markdown."""
    try:
        md = await page.evaluate(_PAGE_TO_MD_JS)
    except Exception as e:
        return {"ok": False, "error": f"md conversion failed: {str(e)[:120]}"}

    if save_path:
        Path(save_path).write_text(md, encoding="utf-8")
        print(f"📝 Markdown 已保存: {save_path} ({len(md):,} chars)")
    else:
        print(md[:8000])
    return {"ok": True, "md": md[:5000], "len": len(md), "path": save_path}


async def cmd_extract_structured(page, container, fields, limit=None, output_file=None):
    """v2.1.5: Extract structured data using container + field selectors.

    fields: dict like {"title": "h2 a", "url": "h2 a @href", "date": ".date"}
    Supports @attr suffix to extract attribute values.
    """
    try:
        items = await page.evaluate("""
            (cfg) => {
                const containers = document.querySelectorAll(cfg.container);
                const results = [];
                const max = cfg.limit ? Math.min(cfg.limit, containers.length) : containers.length;
                for (let i = 0; i < max; i++) {
                    const el = containers[i];
                    const item = {};
                    for (const [key, sel] of Object.entries(cfg.fields)) {
                        const attrMatch = sel.match(/^(.+?)\\s*@(\\w+)$/);
                        if (attrMatch) {
                            const sub = el.querySelector(attrMatch[1]);
                            item[key] = sub ? (sub.getAttribute(attrMatch[2]) || '').trim() : '';
                        } else {
                            const sub = el.querySelector(sel);
                            item[key] = sub ? sub.textContent.trim().replace(/\\s+/g, ' ') : '';
                        }
                    }
                    results.push(item);
                }
                return results;
            }
        """, {"container": container, "fields": fields, "limit": limit})
    except Exception as e:
        return {"ok": False, "error": f"structured extract failed: {str(e)[:120]}"}

    # Print summary
    print(f"📊 提取了 {len(items)} 条记录")
    for i, item in enumerate(items[:5]):
        preview = {k: v[:60] if isinstance(v, str) else v for k, v in item.items()}
        print(f"  [{i}] {json.dumps(preview, ensure_ascii=False)}")
    if len(items) > 5:
        print(f"  ... 还有 {len(items) - 5} 条")

    # Save to file if specified
    if output_file:
        out_path = Path(output_file)
        if not out_path.is_absolute():
            out_path = SKILL_DIR / output_file
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"💾 已保存: {out_path}")

    return {"ok": True, "items": items, "count": len(items), "path": str(output_file) if output_file else None}


async def cmd_html(page, selector=None):
    if selector:
        try:
            html = await page.locator(selector).first.inner_html()
        except Exception:
            html = await page.evaluate(
                f"() => document.querySelector('{selector}')?.innerHTML || ''")
    else:
        html = await page.content()
    print(html[:8000])
    return {"ok": True, "html_len": len(html)}


async def cmd_eval(page, js):
    # v2.0.1: direct evaluate (arrow wrapper with block body had no return → always null)
    result = await page.evaluate(js)
    print(json.dumps(result, ensure_ascii=False, default=str))
    return {"ok": True, "result": result}


# ────────────────── bookmarks ──────────────────

def _load_bookmarks():
    """Load bookmarks list from JSON file."""
    if BOOKMARKS_FILE.exists():
        try:
            return json.loads(BOOKMARKS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def _save_bookmarks(bookmarks):
    BOOKMARKS_FILE.write_text(
        json.dumps(bookmarks, ensure_ascii=False, indent=2), encoding="utf-8")


def cmd_bookmarks(args):
    """Manage bookmarks. Usage:
      bookmarks                         list all
      bookmarks add <name> <url> [desc] add a bookmark
      bookmarks search <keyword>        search by name/desc
      bookmarks remove <n>              remove bookmark by index
    """
    bookmarks = _load_bookmarks()
    sub = args[0] if args else "list"

    if sub == "list":
        if not bookmarks:
            print("📑 暂无书签")
            return {"ok": True, "bookmarks": []}
        print(f"📑 {len(bookmarks)} 个书签:\n")
        for i, b in enumerate(bookmarks):
            tags = ", ".join(b.get("tags", []))
            tag_str = f" [{tags}]" if tags else ""
            print(f"  [{i}] {b['name']}{tag_str}")
            print(f"      {b['url']}")
            if b.get("desc"):
                print(f"      {b['desc']}")
        return {"ok": True, "bookmarks": bookmarks}

    elif sub == "add":
        if len(args) < 3:
            return {"ok": False, "error": "用法: bookmarks add <名称> <URL> [描述]"}
        name, url = args[1], args[2]
        desc = " ".join(args[3:]) if len(args) > 3 else ""
        bookmarks.append({"name": name, "url": url, "desc": desc, "tags": []})
        _save_bookmarks(bookmarks)
        print(f"✅ 已添加书签: {name}")
        return {"ok": True, "action": "add", "name": name}

    elif sub == "search":
        if len(args) < 2:
            return {"ok": False, "error": "用法: bookmarks search <关键词>"}
        keyword = args[1].lower()
        results = [b for b in bookmarks if
                   keyword in b["name"].lower()
                   or keyword in b.get("desc", "").lower()
                   or any(keyword in t.lower() for t in b.get("tags", []))]
        if not results:
            print(f"🔍 未找到匹配 '{keyword}' 的书签")
            return {"ok": True, "matches": []}
        print(f"🔍 找到 {len(results)} 个匹配:\n")
        for i, b in enumerate(results):
            print(f"  [{i}] {b['name']} — {b['url']}")
            if b.get("desc"):
                print(f"      {b['desc']}")
        return {"ok": True, "matches": results}

    elif sub == "remove":
        if len(args) < 2 or not args[1].isdigit():
            return {"ok": False, "error": "用法: bookmarks remove <序号>"}
        idx = int(args[1])
        if idx < 0 or idx >= len(bookmarks):
            return {"ok": False, "error": f"序号 {idx} 超出范围 (0-{len(bookmarks)-1})"}
        removed = bookmarks.pop(idx)
        _save_bookmarks(bookmarks)
        print(f"🗑️ 已移除: {removed['name']}")
        return {"ok": True, "action": "remove", "name": removed["name"]}

    return {"ok": False, "error": f"未知子命令: {sub}"}


# ────────────────── main dispatcher ──────────────────

async def run_action(page, action, args, extras=None):
    """Execute one action, return result dict.
    extras: optional dict with additional params (used in do chains for schema, container, etc.)
    """
    result = {"ok": True}

    if action == "goto":
        url = args[0] if args else ""
        if not url.startswith("http"):
            url = "https://" + url
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(2)
        title = await page.title()
        print(f"📍 {title}")
        print(f"🔗 {page.url}")
        result["summary"] = f"{title} | {page.url}"
        # v2.1.5: log to browsing history
        _append_history(page.url, title)

    elif action == "state":
        return await cmd_state(page)

    elif action == "screenshot":
        name = args[0] if args else None
        return await cmd_screenshot(page, name)

    elif action == "extract":
        # v2.1.5: structured extraction if container+fields provided (in extras for do chains, or args[0] as JSON)
        container = (extras or {}).get("container")
        fields = (extras or {}).get("fields")
        limit = (extras or {}).get("limit")
        output_file = (extras or {}).get("output")
        # Fallback: try first arg as JSON config
        if not container and args:
            try:
                cfg = json.loads(args[0])
                container = cfg.get("container")
                fields = cfg.get("fields")
                limit = cfg.get("limit")
                output_file = cfg.get("output")
            except (json.JSONDecodeError, TypeError):
                pass
        if container and fields:
            return await cmd_extract_structured(page, container, fields, limit, output_file)
        sel = args[0] if args else None
        return await cmd_extract(page, sel)

    elif action == "html":
        sel = args[0] if args else None
        return await cmd_html(page, sel)

    elif action == "click":
        target = str(args[0]) if args else ""
        if target.isdigit():
            idx = int(target)
            state_data = json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.exists() else {}
            elements = state_data.get("elements", [])
            if idx < len(elements):
                selector = elements[idx]["sel"]
                label = elements[idx]["label"]
                href = elements[idx].get("href", "")
                try:
                    await page.locator(selector).first.click(timeout=5000)
                except Exception:
                    try:
                        escaped = selector.replace("'", "\\'")
                        await page.evaluate(
                            f"const el=document.querySelector('{escaped}');if(el)el.click();else throw new Error('not found');")
                    except Exception:
                        if href:
                            await page.goto(href, wait_until="domcontentloaded", timeout=15000)
                            await asyncio.sleep(1)
                            result["summary"] = f"navigated [{idx}] {label}"
                            return result
                        raise
                result["summary"] = f"clicked [{idx}] {label}"
            else:
                result = {"ok": False, "error": f"index {idx} out of range (0-{len(elements)-1})"}
        elif target.startswith("iframe:"):
            sel = target[7:]
            f = page.frame_locator("iframe")
            el = f.locator(f'[aria-label*="{sel}"]')
            if await el.count() > 0:
                clickable = f.locator(f'[aria-label*="{sel}"]').locator('..').locator('..').locator('..').locator('..').locator('..')
                await clickable.first.click(timeout=5000, force=True)
            else:
                await f.locator(f'text="{sel}"').first.click(timeout=5000, force=True)
            result["summary"] = f"clicked iframe element '{sel[:40]}'"
        else:
            loc = page.locator(target).first
            wait_nav = len(args) > 1 and args[1] == "--wait-nav"
            if wait_nav:
                async with page.expect_navigation(wait_until="domcontentloaded", timeout=15000):
                    await loc.click(timeout=5000)
                result["summary"] = f"clicked {target} (waited for nav)"
            else:
                await loc.click(timeout=5000)
                result["summary"] = f"clicked {target}"
        await asyncio.sleep(1)

    elif action == "dblclick":
        # v2.0.3: double-click for SPAs that need it (OWA opens email on dblclick)
        target = str(args[0]) if args else ""
        if target.isdigit():
            idx = int(target)
            state_data = json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.exists() else {}
            elements = state_data.get("elements", [])
            if idx < len(elements):
                selector = elements[idx]["sel"]
                label = elements[idx]["label"]
                try:
                    await page.locator(selector).first.dblclick(timeout=5000)
                except Exception:
                    # fallback: JS dblclick event
                    escaped = selector.replace("'", "\\'")
                    await page.evaluate(
                        f"const el=document.querySelector('{escaped}');"
                        f"if(el){{el.dispatchEvent(new MouseEvent('dblclick',{{bubbles:true,cancelable:true,view:window}}));}}"
                        f"else{{throw new Error('not found');}}")
                result["summary"] = f"double-clicked [{idx}] {label}"
            else:
                result = {"ok": False, "error": f"index {idx} out of range (0-{len(elements)-1})"}
        else:
            await page.locator(target).first.dblclick(timeout=5000)
            result["summary"] = f"double-clicked {target}"
        await asyncio.sleep(1)

    elif action == "type":
        if len(args) >= 2 and (args[0].startswith("#") or args[0].startswith(".") or args[0].startswith("[")):
            sel, text = args[0], " ".join(args[1:])
            await page.locator(sel).first.fill(text, timeout=5000)
            result["summary"] = f"typed into {sel}"
        else:
            text = " ".join(args)
            await page.keyboard.type(text)
            result["summary"] = f"typed '{text[:30]}'"

    elif action == "press":
        key = args[0] if args else "Enter"
        await page.keyboard.press(key)
        result["summary"] = f"pressed {key}"

    elif action == "manual":
        msg = args[0] if args else "请在浏览器中完成操作"
        timeout_sec = int(args[1]) if len(args) > 1 else 600
        signal_file = RUNTIME_DIR / ".manual_done"
        if signal_file.exists():
            signal_file.unlink()
        print(f"\n👆 {msg}", flush=True)
        print(f"⏳ 等待信号文件 (timeout: {timeout_sec}s)...", flush=True)
        waited = 0
        while not signal_file.exists():
            if waited >= timeout_sec:
                result = {"ok": False, "error": f"manual step timed out after {timeout_sec}s"}
                return result
            await asyncio.sleep(1)
            waited += 1
        signal_file.unlink()
        result["summary"] = "manual step done"

    elif action == "scroll":
        direction = args[0] if args else "down"
        px = int(args[1]) if len(args) > 1 else 500
        if direction == "down":
            await page.evaluate(f"window.scrollBy(0, {px})")
        else:
            await page.evaluate(f"window.scrollBy(0, -{px})")
        result["summary"] = f"scrolled {direction} {px}px"

    elif action == "download":
        if args and args[0].startswith("http"):
            url = args[0]
            save_path = args[1] if len(args) > 1 else str(DOWNLOADS_DIR / os.path.basename(url.rstrip("/").split("?")[0]))
            os.makedirs(os.path.dirname(save_path), exist_ok=True)
            try:
                async with page.expect_download(timeout=30000) as di:
                    await page.goto(url, timeout=15000, wait_until="commit")
                download = await di.value
                await download.save_as(save_path)
                result["summary"] = f"downloaded {os.path.basename(save_path)} ({os.path.getsize(save_path)} bytes)"
                result["path"] = save_path
            except Exception as e:
                result = {"ok": False, "error": f"download: {str(e)[:120]}"}
        else:
            target = args[0] if args else "a[href*='.rar'], a[href*='.zip']"
            save_path = args[1] if len(args) > 1 else None
            try:
                async with page.expect_download(timeout=30000) as di:
                    await page.locator(target).first.click(timeout=5000)
                download = await di.value
                if save_path:
                    os.makedirs(os.path.dirname(save_path), exist_ok=True)
                else:
                    save_path = str(DOWNLOADS_DIR / download.suggested_filename)
                await download.save_as(save_path)
                result["summary"] = f"downloaded {download.suggested_filename} ({os.path.getsize(save_path)} bytes)"
                result["path"] = save_path
            except Exception as e:
                result = {"ok": False, "error": f"download: {str(e)[:120]}"}

    elif action == "mouse_click":
        x = int(args[0]) if len(args) > 0 else 0
        y = int(args[1]) if len(args) > 1 else 0
        await page.mouse.click(x, y)
        result["summary"] = f"mouse click at ({x}, {y})"

    elif action == "wait":
        # v2.1.5: extended conditional wait
        if args and isinstance(args[0], dict):
            # Do chain mode: structured config
            cfg = args[0]
            kind = cfg.get("kind", "")
            value = cfg.get("value", "")
            timeout = cfg.get("timeout", 30000)
        elif args and str(args[0]).startswith("--"):
            # CLI flag mode
            flag_map = {"--text": "text", "--js": "js", "--network-idle": "network_idle",
                        "--title": "title", "--url": "url"}
            kind = flag_map.get(str(args[0]), "")
            value = str(args[1]) if len(args) > 1 else ""
            timeout = 30000
        else:
            # Legacy: wait ms or selector
            target = str(args[0]) if args else "2000"
            if target.isdigit():
                ms = int(target)
                await asyncio.sleep(ms / 1000)
                result["summary"] = f"waited {ms}ms"
                return result
            else:
                await page.wait_for_selector(target, timeout=10000)
                result["summary"] = f"waited for {target}"
                return result

        # Conditional wait implementations
        if kind == "text":
            await page.wait_for_function(
                f"""() => document.body.innerText.includes({json.dumps(value)})""", timeout=timeout)
            result["summary"] = f"waited for text '{value[:40]}'"
        elif kind == "js":
            await page.wait_for_function(value, timeout=timeout)
            result["summary"] = "waited for js condition"
        elif kind == "network_idle":
            await page.wait_for_load_state("networkidle", timeout=timeout)
            result["summary"] = "waited for network idle"
        elif kind == "title":
            if value:
                await page.wait_for_function(
                    f"""() => document.title.includes({json.dumps(value)})""", timeout=timeout)
            else:
                old_title = await page.title()
                await page.wait_for_function(
                    f"""() => document.title !== {json.dumps(old_title)}""", timeout=timeout)
            result["summary"] = f"waited for title '{value[:40]}'"
        elif kind == "url":
            if value:
                pattern = value if value.startswith("http") else f"**{value}**"
                await page.wait_for_url(pattern, timeout=timeout)
            else:
                old_url = page.url
                await page.wait_for_function(
                    f"""() => window.location.href !== {json.dumps(old_url)}""", timeout=timeout)
            result["summary"] = f"waited for url '{value[:40]}'"
        else:
            result = {"ok": False, "error": f"unknown wait kind: {kind}"}

    elif action == "tabs":
        sub = args[0] if args else "list"
        if sub == "list":
            pages = page.context.pages
            for i, p in enumerate(pages):
                marker = "◀" if p == page else " "
                print(f"  [{i}] {marker} {(await p.title())[:60]}")
            result["summary"] = f"{len(pages)} tabs"
        elif sub == "switch":
            idx = int(args[1]) if len(args) > 1 else 0
            pages = page.context.pages
            if idx < len(pages):
                new_page = pages[idx]
                await new_page.bring_to_front()
                result["summary"] = f"switched to tab {idx}"
        elif sub == "new":
            await page.context.new_page()
            result["summary"] = "new tab opened"
        elif sub == "close":
            await page.close()
            result["summary"] = "tab closed"

    elif action == "eval_iframe":
        js = " ".join(args)
        f = page.frame_locator("iframe")
        el = f.locator("html")
        res = await el.evaluate(f"() => {{ {js} }}")
        print(json.dumps(res, ensure_ascii=False, default=str))
        result["result"] = res
        result["summary"] = str(res)[:80]

    elif action == "eval":
        return await cmd_eval(page, " ".join(args))

    elif action == "md":
        # v2.1.5: convert current page to markdown
        save_path = args[0] if args else None
        return await cmd_md(page, save_path)

    elif action == "pdf_save":
        # Determine URL and save path
        if args and args[0].startswith("http"):
            pdf_url = args[0]
            save_path = args[1] if len(args) > 1 else None
        else:
            pdf_url = page.url
            save_path = args[0] if args else None

        if not save_path:
            from urllib.parse import urlparse, unquote
            parsed = urlparse(pdf_url)
            filename = unquote(os.path.basename(parsed.path)) or "document.pdf"
            if not filename.lower().endswith('.pdf'):
                filename += '.pdf'
            save_path = str(DOWNLOADS_DIR / filename)

        os.makedirs(os.path.dirname(save_path), exist_ok=True)

        try:
            import base64
            pdf_bytes_b64 = await page.evaluate("""async (url) => {
                const resp = await fetch(url);
                if (!resp.ok) throw new Error('HTTP ' + resp.status);
                const blob = await resp.blob();
                return new Promise((resolve, reject) => {
                    const reader = new FileReader();
                    reader.onload = () => resolve(reader.result.split(',')[1]);
                    reader.onerror = () => reject(new Error('FileReader failed'));
                    reader.readAsDataURL(blob);
                });
            }""", pdf_url)
            pdf_bytes = base64.b64decode(pdf_bytes_b64)
            Path(save_path).write_bytes(pdf_bytes)
            size = len(pdf_bytes)
            print(f"📄 PDF 已保存: {save_path} ({size:,} bytes)")
            result = {"ok": True, "path": save_path, "size": size, "summary": f"PDF saved ({size:,} bytes)"}
        except Exception as e:
            result = {"ok": False, "error": f"PDF save failed: {str(e)[:120]}"}

    elif action == "close":
        result["summary"] = "closing browser"
    else:
        result = {"ok": False, "error": f"unknown action: {action}"}

    return result


# ────────────────── start / stop ──────────────────

async def cmd_start():
    """Launch Chrome as an independent process with CDP enabled."""
    if _check_chrome_running():
        port_info = ""
        try:
            import urllib.request
            resp = json.loads(urllib.request.urlopen(f"{CDP_URL}/json/version", timeout=2).read())
            port_info = f" | {resp.get('Browser', 'Chrome')[:40]}"
        except Exception:
            pass
        print(f"🟢 Chrome 已在运行 (port {CDP_PORT}{port_info})")
        return

    chrome_exe = await _get_chrome_exe()
    print(f"🔧 启动 Chrome: {chrome_exe}")

    proc = subprocess.Popen(
        [
            chrome_exe,
            f"--remote-debugging-port={CDP_PORT}",
            f"--user-data-dir={USER_DATA}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-blink-features=AutomationControlled",
            "--disable-features=Translate",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    PID_FILE.write_text(str(proc.pid))

    # Wait for CDP to be ready
    for i in range(60):
        await asyncio.sleep(0.5)
        if _check_chrome_running():
            print(f"🟢 Chrome 已启动 (PID: {proc.pid}, port {CDP_PORT})")
            print(f"📁 user_data: {USER_DATA}")
            return

    print("❌ Chrome 启动超时 (30s)")
    PID_FILE.unlink(missing_ok=True)
    sys.exit(1)


async def cmd_stop():
    """Gracefully close Chrome via CDP, fallback to taskkill."""
    if not _check_chrome_running():
        print("ℹ️ Chrome 未运行")
        PID_FILE.unlink(missing_ok=True)
        return

    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp(CDP_URL)
            # Close all contexts' pages then close browser
            for ctx in browser.contexts:
                for pg in ctx.pages:
                    await pg.close()
            await browser.close()
    except Exception:
        pass

    # Fallback: kill by pid
    try:
        if PID_FILE.exists():
            pid = int(PID_FILE.read_text().strip())
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                os.kill(pid, signal.SIGTERM)
    except Exception:
        pass

    PID_FILE.unlink(missing_ok=True)
    _check_chrome_running()  # wait
    print("👋 Chrome 已关闭")


# ────────────────── run modes ──────────────────

async def run_single(action, args):
    """Single command: connect → execute → disconnect."""
    from playwright.async_api import async_playwright

    if not _check_chrome_running():
        print("❌ Chrome 未运行。请先执行: py agent_browser.py start")
        sys.exit(1)

    async with async_playwright() as p:
        browser, page = await _connect(p)
        result = await run_action(page, action, args)
        await _log_cmd(action, args, result)

        if action not in ("state", "screenshot", "extract", "html", "eval", "tabs"):
            if result.get("ok"):
                print(f"✅ {result.get('summary', 'done')}")
            else:
                print(f"❌ {result.get('error', 'unknown error')}")

    if not result.get("ok"):
        sys.exit(1)


async def _handle_error(page, cmd, result):
    """Apply error recovery strategy for a failed step.
    Returns (final_result, should_stop).
    """
    on_error = cmd.get("on_error", "stop")  # default: stop (backward compatible)
    extras = {k: v for k, v in cmd.items() if k not in ("action", "args", "on_error", "fallback", "retry", "retry_delay_ms")}

    if on_error == "stop":
        return result, True

    elif on_error == "skip":
        print(f"⚠️  Error skipped: {result.get('error','')[:60]}")
        result["_recovered"] = "skip"
        return result, False

    elif on_error == "retry":
        max_retries = cmd.get("retry", 1)
        delay_ms = cmd.get("retry_delay_ms", 2000)
        for attempt in range(1, max_retries + 1):
            print(f"🔄 Retry {attempt}/{max_retries} after {delay_ms}ms...")
            await asyncio.sleep(delay_ms / 1000)
            action = cmd.get("action", "")
            args = cmd.get("args", [])
            result = await run_action(page, action, args)
            if result.get("ok"):
                result["_recovered"] = f"retry_{attempt}"
                print(f"✅ Retry {attempt} succeeded")
                return result, False
        print(f"❌ All {max_retries} retries failed")
        result["_recovered"] = "retry_exhausted"
        return result, False

    elif on_error == "fallback":
        fallback = cmd.get("fallback")
        if fallback and isinstance(fallback, dict):
            fb_action = fallback.get("action", "")
            fb_args = fallback.get("args", [])
            print(f"🔀 Fallback: {fb_action} {' '.join(fb_args)[:40]}")
            try:
                fb_result = await run_action(page, fb_action, fb_args)
                if fb_result.get("ok"):
                    result["_recovered"] = "fallback_ok"
                    result["_fallback_summary"] = fb_result.get("summary", "")
                    print(f"✅ Fallback succeeded: {fb_result.get('summary','')[:60]}")
                    return result, False
                else:
                    result["_recovered"] = "fallback_failed"
                    result["_fallback_error"] = fb_result.get("error", "")
                    print(f"❌ Fallback also failed: {fb_result.get('error','')[:60]}")
            except Exception as e:
                result["_recovered"] = "fallback_error"
                result["_fallback_error"] = str(e)[:120]
                print(f"❌ Fallback exception: {e}")
        else:
            print(f"⚠️  on_error=fallback but no fallback action defined")
            result["_recovered"] = "fallback_missing"
        return result, False

    else:
        print(f"⚠️  Unknown on_error='{on_error}', treating as 'stop'")
        return result, True


async def run_chain(actions_file, resume_from=0):
    """Chain multiple actions in one CDP connection.

    Per-step error handling (new in v2.1.4):
      "on_error": "stop"    — halt the chain (default, backward compatible)
      "on_error": "skip"    — log error and continue
      "on_error": "retry"   — retry N times (use "retry" and "retry_delay_ms")
      "on_error": "fallback" — execute fallback action if main fails

    Structured extraction (new in v2.1.5):
      {"action":"extract","container":"article.item","fields":{"title":"h2 a","url":"h2 a @href"},"limit":10,"output":"items.json"}

    Example:
      {"action":"click","args":["5"],"on_error":"retry","retry":2,"retry_delay_ms":3000}
      {"action":"click","args":["#submit"],"on_error":"fallback","fallback":{"action":"press","args":["Enter"]}}
    """
    from playwright.async_api import async_playwright

    raw = Path(actions_file).read_text(encoding="utf-8").strip()
    actions = json.loads(raw)

    if not _check_chrome_running():
        print("🔧 Chrome 未运行，正在自动启动...")
        await cmd_start()

    async with async_playwright() as p:
        browser, page = await _connect(p)
        results = []
        stopped = False
        for i, cmd in enumerate(actions):
            if i < resume_from:
                print(f"\n── [{i+1}/{len(actions)}] {cmd.get('action','')} (skipped) ──")
                results.append({"ok": True, "summary": "skipped (resume)", "_skip": True})
                continue
            action = cmd.get("action", "")
            args = cmd.get("args", [])
            print(f"\n── [{i+1}/{len(actions)}] {action} {' '.join(str(a) for a in args)[:50]} ──")

            # v2.1.5: pass extra keys as extras for structured extract, etc.
            extras = {k: v for k, v in cmd.items() if k not in ("action", "args", "on_error", "fallback", "retry", "retry_delay_ms")}
            try:
                result = await run_action(page, action, args, extras)
            except Exception as e:
                result = {"ok": False, "error": str(e)[:200]}

            # Apply error recovery if failed
            if not result.get("ok"):
                print(f"❌ Failed: {result.get('error','')[:80]}")
                result, should_stop = await _handle_error(page, cmd, result)
                results.append(result)
                if should_stop:
                    print(f"🛑 Chain stopped at step {i+1}")
                    stopped = True
                    break
            else:
                results.append(result)

        if not stopped:
            ok_count = sum(1 for r in results if r.get("ok"))
            recovered = sum(1 for r in results if r.get("_recovered"))
            print(f"\n🎉 Chain complete: {ok_count}/{len(results)} steps ok" + (f" ({recovered} recovered)" if recovered else ""))

    total = len(actions)
    ok_count = sum(1 for r in results if r.get("ok"))
    recovered = sum(1 for r in results if r.get("_recovered"))
    print(f"\n{'='*40}")
    print(f"📊 {ok_count}/{len(results)} of {total} steps ok" + (f" ({recovered} recovered)" if recovered else ""))
    for i, r in enumerate(results):
        rec = f" [↻ {r['_recovered']}]" if r.get("_recovered") else ""
        status = "✅" if r.get("ok") else "❌"
        summary = r.get('summary', r.get('error',''))[:80]
        extra = r.get('_skip') and " (skipped)" or ""
        print(f"  [{i}] {status}{rec} {actions[i].get('action','')}: {summary}{extra}")
    return results


# ────────────────── watch mode (file-based IPC) ──────────────────

CMD_FILE = RUNTIME_DIR / "cmd.json"
RESP_FILE = RUNTIME_DIR / "resp.json"
WATCH_PID_FILE = RUNTIME_DIR / "watch.pid"


async def run_watch():
    """Daemon: connect to existing Chrome, poll cmd.json for instructions."""
    from playwright.async_api import async_playwright

    WATCH_PID_FILE.write_text(str(os.getpid()))
    if CMD_FILE.exists():
        CMD_FILE.unlink()

    if not _check_chrome_running():
        print("🔧 Chrome 未运行，正在自动启动...", flush=True)
        await cmd_start()

    async with async_playwright() as p:
        browser, page = await _connect(p)

        print("🟢 Agent Browser WATCH 已启动", flush=True)
        print(f"📁 PID: {os.getpid()} | user_data: {USER_DATA}", flush=True)
        print(f"📁 指令: {CMD_FILE}", flush=True)
        print("READY", flush=True)

        seq = 0
        while True:
            try:
                if CMD_FILE.exists():
                    try:
                        cmd_raw = CMD_FILE.read_text(encoding="utf-8").strip()
                        CMD_FILE.unlink()
                        if not cmd_raw:
                            continue
                        if cmd_raw == "exit":
                            break
                        cmd = json.loads(cmd_raw)
                    except (json.JSONDecodeError, FileNotFoundError):
                        continue

                    action = cmd.get("action", "")
                    args = cmd.get("args", [])

                    result = await run_action(page, action, args)
                    await _log_cmd(action, args, result)

                    result["_seq"] = seq
                    result["_action"] = action
                    RESP_FILE.write_text(
                        json.dumps(result, ensure_ascii=False, default=str),
                        encoding="utf-8")
                    seq += 1

                await asyncio.sleep(0.5)

            except KeyboardInterrupt:
                break
            except Exception as e:
                err = {"_seq": seq, "ok": False, "error": str(e)}
                RESP_FILE.write_text(
                    json.dumps(err, ensure_ascii=False, default=str),
                    encoding="utf-8")
                seq += 1

        WATCH_PID_FILE.unlink(missing_ok=True)
        print("👋 Bye", flush=True)


# ────────────────── main ──────────────────

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    action = sys.argv[1]
    args = sys.argv[2:]

    if action == "start":
        asyncio.run(cmd_start())
    elif action == "stop":
        asyncio.run(cmd_stop())
    elif action == "watch":
        asyncio.run(run_watch())
    elif action == "do":
        resume_from = 0
        remaining = []
        for a in args:
            if a.startswith("--resume-from=") or a.startswith("-r="):
                resume_from = int(a.split("=")[1])
            else:
                remaining.append(a)
        if remaining:
            asyncio.run(run_chain(remaining[0], resume_from))
    elif action == "bookmarks":
        result = cmd_bookmarks(args)
        if not result.get("ok"):
            print(f"❌ {result.get('error', 'unknown error')}")
            sys.exit(1)
    else:
        asyncio.run(run_single(action, args))


if __name__ == "__main__":
    main()
