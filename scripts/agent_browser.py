#!/usr/bin/env py
# -*- coding: utf-8 -*-
"""
Agent Browser v1.1.0 — AI-controllable persistent Chrome
Usage:
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
  py agent_browser.py watch                # daemon mode (JSON stdin)
  py agent_browser.py close
"""

import asyncio
import json
import os
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
COOKIE_FILE = SCRIPT_DIR / ".browser_cookies.json"
LOG_DIR = SKILL_DIR / "logs"

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
            const role = el.getAttribute('role') || '';
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
        "url": page.url,
        "title": await page.title(),
        "elements": elements,
        "visible": len([e for e in elements if e["visible"]]),
        "total": len(elements)
    }

    # Save state
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"📍 {state['title']}")
    print(f"🔗 {state['url']}")
    print(f"🧩 {state['visible']}/{state['total']} 交互元素\n")

    for e in elements:
        marker = "👁" if e["visible"] else "⬇"
        tag_info = e["tag"]
        print(f"  [{e['i']}] {marker} {tag_info} | {e['label'][:70]}")

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
            el = page.locator(selector).first
            text = await el.inner_text()
        except Exception:
            text = ""
        if not text:
            text = await page.evaluate(f"""(sel) => {{
                const el = document.querySelector(sel);
                return el ? el.textContent.trim() : '';
            }}""", selector)
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
            html = await page.evaluate(f"() => document.querySelector('{selector}')?.innerHTML || ''")
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
            # Click by state index
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
                        await page.evaluate(f"const el=document.querySelector('{escaped}');if(el)el.click();else throw new Error('not found');")
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
            # Click inside iframe: matches text, clicks closest clickable ancestor
            sel = target[7:]
            f = page.frame_locator("iframe")
            el = f.locator(f'[aria-label*="{sel}"]')
            if await el.count() > 0:
                # Click the closest clickable ancestor (5 levels up to the tabindex div)
                clickable = f.locator(f'[aria-label*="{sel}"]').locator('..').locator('..').locator('..').locator('..').locator('..')
                # Verify it has tabindex
                await clickable.first.click(timeout=5000, force=True)
            else:
                await f.locator(f'text="{sel}"').first.click(timeout=5000, force=True)
            result["summary"] = f"clicked iframe element '{sel[:40]}'"
        else:
            # Click by CSS selector; --wait-nav waits for page navigation after click
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
        if len(args) >= 2 and args[0].startswith("#") or args[0].startswith(".") or args[0].startswith("["):
            # selector + text
            sel, text = args[0], " ".join(args[1:])
            await page.locator(sel).first.fill(text, timeout=5000)
            result["summary"] = f"typed into {sel}"
        else:
            # Just text into focused element
            text = " ".join(args)
            await page.keyboard.type(text)
            result["summary"] = f"typed '{text[:30]}'"

    elif action == "press":
        key = args[0] if args else "Enter"
        await page.keyboard.press(key)
        result["summary"] = f"pressed {key}"

    elif action == "manual":
        # Pause for manual interaction, wait for signal file
        msg = args[0] if args else "请在浏览器中完成操作"
        signal_file = SCRIPT_DIR / ".manual_done"
        if signal_file.exists():
            signal_file.unlink()
        print(f"\n👆 {msg}", flush=True)
        print(f"⏳ 完成后我会通过信号文件通知继续...", flush=True)
        while not signal_file.exists():
            await asyncio.sleep(1)
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
        # Click a link that triggers a download
        if args and args[0].startswith("http"):
            # Direct URL: navigate and capture download
            url = args[0]
            save_path = args[1] if len(args) > 1 else os.path.join(os.path.dirname(__file__), "..", "..", "..", "downloads", os.path.basename(url.rstrip("/").split("?")[0]))
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
            # Click selector to trigger download
            target = args[0] if args else "a[href*='.rar'], a[href*='.zip']"
            save_path = args[1] if len(args) > 1 else None
            os.makedirs(os.path.join(os.path.dirname(__file__), "..", "..", "..", "downloads"), exist_ok=True)
            try:
                async with page.expect_download(timeout=30000) as di:
                    await page.locator(target).first.click(timeout=5000)
                download = await di.value
                if save_path:
                    os.makedirs(os.path.dirname(save_path), exist_ok=True)
                else:
                    save_path = os.path.join(os.path.dirname(__file__), "..", "..", "..", "downloads", download.suggested_filename)
                await download.save_as(save_path)
                result["summary"] = f"downloaded {download.suggested_filename} ({os.path.getsize(save_path)} bytes)"
                result["path"] = save_path
            except Exception as e:
                result = {"ok": False, "error": f"download: {str(e)[:120]}"}

    elif action == "mouse_click":
        # Click at absolute x,y coordinates
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
            # Wait for selector
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
            new_page = await page.context.new_page()
            result["summary"] = "new tab opened"
        elif sub == "close":
            await page.close()
            result["summary"] = "tab closed"

    elif action == "eval_iframe":
        # Run JS inside the first iframe
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
        # Handled in main

    else:
        result = {"ok": False, "error": f"unknown action: {action}"}

    return result


# ────────────────── browser helpers ──────────────────

async def _save_cookies(context):
    cookies = await context.cookies()
    COOKIE_FILE.write_text(json.dumps(cookies, ensure_ascii=False), encoding="utf-8")


async def _new_browser_context(p):
    browser = await p.chromium.launch(
        headless=False,
        args=["--disable-blink-features=AutomationControlled"],
    )
    # Use default context (more stable than new_context on Windows)
    context = None
    if COOKIE_FILE.exists():
        try:
            cookies = json.loads(COOKIE_FILE.read_text(encoding="utf-8"))
            context = await browser.new_context(
                viewport={"width": 1280, "height": 800},
                locale="zh-CN",
            )
            await context.add_cookies(cookies)
        except Exception:
            pass
    if not context:
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            locale="zh-CN",
        )
    return browser, context


# ────────────────── single command mode ──────────────────

async def run_single(action, args):
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch_persistent_context(
            user_data_dir=str(USER_DATA),
            headless=False,
            viewport={"width": 1280, "height": 800},
            locale="zh-CN",
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = browser.pages[0] if browser.pages else await browser.new_page()

        result = await run_action(page, action, args)
        await _log_cmd(action, args, result)

        if action not in ("state", "screenshot", "extract", "html", "eval", "tabs"):
            if result.get("ok"):
                print(f"✅ {result.get('summary', 'done')}")
            else:
                print(f"❌ {result.get('error', 'unknown error')}")

        if action != "close":
            await asyncio.sleep(1)

        await browser.close()

    if not result.get("ok"):
        sys.exit(1)


# ────────────────── watch mode (file-based IPC) ──────────────────

CMD_FILE = SCRIPT_DIR / "cmd.json"
RESP_FILE = SCRIPT_DIR / "resp.json"
PID_FILE = SCRIPT_DIR / "watch.pid"


async def run_watch():
    from playwright.async_api import async_playwright

    PID_FILE.write_text(str(os.getpid()))
    if CMD_FILE.exists():
        CMD_FILE.unlink()

    async with async_playwright() as p:
        browser = await p.chromium.launch_persistent_context(
            user_data_dir=str(USER_DATA),
            headless=False,
            viewport={"width": 1280, "height": 800},
            locale="zh-CN",
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = browser.pages[0] if browser.pages else await browser.new_page()

        # Navigate to ehall immediately to trigger SSO
        print("🔗 Navigating to ehall...", flush=True)
        await page.goto("https://ehall.xjtlu.edu.cn/default/index.html#/homeXS")
        await asyncio.sleep(2)
        print(f"📍 {page.url}", flush=True)

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
                        if not cmd_raw or cmd_raw == "exit":
                            if cmd_raw == "exit":
                                break
                            continue
                        cmd = json.loads(cmd_raw)
                    except (json.JSONDecodeError, FileNotFoundError):
                        continue

                    action = cmd.get("action", "")
                    args = cmd.get("args", [])

                    result = await run_action(page, action, args)
                    await _log_cmd(action, args, result)

                    result["_seq"] = seq
                    result["_action"] = action
                    RESP_FILE.write_text(json.dumps(result, ensure_ascii=False, default=str),
                                        encoding="utf-8")
                    seq += 1

                await asyncio.sleep(0.5)

            except KeyboardInterrupt:
                break
            except Exception as e:
                err = {"_seq": seq, "ok": False, "error": str(e)}
                RESP_FILE.write_text(json.dumps(err, ensure_ascii=False, default=str),
                                    encoding="utf-8")
                seq += 1

        await browser.close()
        PID_FILE.unlink(missing_ok=True)
        print("👋 Bye", flush=True)


# ────────────────── do mode (chain multiple actions in one session) ──────────────────

async def run_chain(actions_file, resume_from=0):
    """Chain multiple actions in one browser session.
    actions_file: path to a JSON file with action list.
    Format: [{"action": "goto", "args": ["url"]}, {"action": "state"}, ...]
    """
    from playwright.async_api import async_playwright

    raw = Path(actions_file).read_text(encoding="utf-8")
    # Strip leading/trailing array brackets if wrapped in them
    raw = raw.strip()
    actions = json.loads(raw)

    async with async_playwright() as p:
        browser = await p.chromium.launch_persistent_context(
            user_data_dir=str(USER_DATA),
            headless=False,
            viewport={"width": 1280, "height": 800},
            locale="zh-CN",
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = browser.pages[0] if browser.pages else await browser.new_page()

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

        await browser.close()

    # Print summary
    print(f"\n{'='*40}")
    for i, r in enumerate(results):
        status = "✅" if r.get("ok") else "❌"
        print(f"  [{i}] {status} {actions[i].get('action','')}: {r.get('summary', r.get('error',''))[:80]}")

    return results


# ────────────────── main ──────────────────

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    action = sys.argv[1]
    args = sys.argv[2:]

    if action == "watch":
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
