#!/usr/bin/env py
# -*- coding: utf-8 -*-
"""
Agent Browser v2.0.0 — AI-controllable browser via CDP
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
  py agent_browser.py extract [selector]
  py agent_browser.py html [selector]
  py agent_browser.py scroll <up|down> [px]
  py agent_browser.py wait <ms|selector>
  py agent_browser.py eval <js>
  py agent_browser.py tabs [list|switch <n>|new|close]
  py agent_browser.py watch                # daemon mode (file IPC)
  py agent_browser.py do <plan.json> [--resume-from=N]

Architecture:
  Chrome runs as an independent process (start/stop).
  All other commands connect via CDP — process killed ≠ Chrome killed.
  user_data/ persists cookies across sessions.
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
USER_DATA = SKILL_DIR / "user_data"
STATE_FILE = SCRIPT_DIR / "state.json"
LOG_DIR = SKILL_DIR / "logs"
CHROME_EXE_CACHE = SCRIPT_DIR / ".chrome_exe_path"
PID_FILE = SCRIPT_DIR / "chrome.pid"
CDP_PORT = 9222
CDP_URL = f"http://localhost:{CDP_PORT}"

USER_DATA.mkdir(parents=True, exist_ok=True)
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
    filename = name or f"screenshot_{int(time.time())}"
    path = str(SCRIPT_DIR / f"{filename}.jpg")
    await page.screenshot(path=path, type="jpeg", quality=85, full_page=False)
    print(f"📸 {path}")
    return {"ok": True, "path": path}


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
    result = await page.evaluate(f"() => {{ {js} }}")
    print(json.dumps(result, ensure_ascii=False, default=str))
    return {"ok": True, "result": result}


# ────────────────── main dispatcher ──────────────────

async def run_action(page, action, args):
    """Execute one action, return result dict."""
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

    elif action == "state":
        return await cmd_state(page)

    elif action == "screenshot":
        name = args[0] if args else None
        return await cmd_screenshot(page, name)

    elif action == "extract":
        sel = args[0] if args else None
        return await cmd_extract(page, sel)

    elif action == "html":
        sel = args[0] if args else None
        return await cmd_html(page, sel)

    elif action == "click":
        target = args[0] if args else ""
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
        signal_file = SCRIPT_DIR / ".manual_done"
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
            save_path = args[1] if len(args) > 1 else os.path.join(
                os.path.dirname(__file__), "..", "..", "..", "downloads",
                os.path.basename(url.rstrip("/").split("?")[0]))
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
            dl_dir = os.path.join(os.path.dirname(__file__), "..", "..", "..", "downloads")
            os.makedirs(dl_dir, exist_ok=True)
            try:
                async with page.expect_download(timeout=30000) as di:
                    await page.locator(target).first.click(timeout=5000)
                download = await di.value
                if save_path:
                    os.makedirs(os.path.dirname(save_path), exist_ok=True)
                else:
                    save_path = os.path.join(dl_dir, download.suggested_filename)
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
        target = args[0] if args else "2000"
        if target.isdigit():
            ms = int(target)
            await asyncio.sleep(ms / 1000)
            result["summary"] = f"waited {ms}ms"
        else:
            await page.wait_for_selector(target, timeout=10000)
            result["summary"] = f"waited for {target}"

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


async def run_chain(actions_file, resume_from=0):
    """Chain multiple actions in one CDP connection."""
    from playwright.async_api import async_playwright

    raw = Path(actions_file).read_text(encoding="utf-8").strip()
    actions = json.loads(raw)

    if not _check_chrome_running():
        print("❌ Chrome 未运行。正在自动启动...")
        await cmd_start()

    async with async_playwright() as p:
        browser, page = await _connect(p)
        results = []
        for i, cmd in enumerate(actions):
            if i < resume_from:
                print(f"\n── [{i+1}/{len(actions)}] {cmd.get('action','')} (skipped) ──")
                results.append({"ok": True, "summary": "skipped (resume)", "_skip": True})
                continue
            action = cmd.get("action", "")
            args = cmd.get("args", [])
            print(f"\n── [{i+1}/{len(actions)}] {action} {' '.join(args)[:50]} ──")
            result = await run_action(page, action, args)
            results.append(result)
            if not result.get("ok"):
                print(f"❌ Stopping chain: {result.get('error', 'failed')}")
                break

    print(f"\n{'='*40}")
    for i, r in enumerate(results):
        status = "✅" if r.get("ok") else "❌"
        print(f"  [{i}] {status} {actions[i].get('action','')}: {r.get('summary', r.get('error',''))[:80]}")
    return results


# ────────────────── watch mode (file-based IPC) ──────────────────

CMD_FILE = SCRIPT_DIR / "cmd.json"
RESP_FILE = SCRIPT_DIR / "resp.json"
WATCH_PID_FILE = SCRIPT_DIR / "watch.pid"


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
    else:
        asyncio.run(run_single(action, args))


if __name__ == "__main__":
    main()
