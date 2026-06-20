import json
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print(json.dumps({"error": "playwright not found"}, ensure_ascii=False))
    sys.exit(1)

JST = timezone(timedelta(hours=9))

LOCATIONS = [
    "https://smartgolf.stores.jp/reserve/smartgolf_kitashinjuku/3421038/book/course_type",
    "https://smartgolf.stores.jp/reserve/smartgolf_nakanoshimbashi/1459178/book/course_type",
    "https://smartgolf.stores.jp/reserve/smartgolf_shinnakano/4619269/book/course_type",
]

# ── 並列プログレス管理 ─────────────────────────────────────────────────────
_progress_lock = threading.Lock()
_progress_state: dict[str, tuple[int, str]] = {}  # loc_name → (pct, message)

def _flush_progress() -> None:
    """全店の進捗を平均して1行出力する（ロック内で呼ぶこと）。"""
    if not _progress_state:
        return
    overall = int(sum(v[0] for v in _progress_state.values()) / len(LOCATIONS))
    msgs = [v[1] for v in _progress_state.values() if v[1]]
    print(json.dumps(
        {"_progress": overall, "_message": " | ".join(msgs)},
        ensure_ascii=False,
    ), flush=True)

def update_progress(loc_name: str, pct: int, msg: str) -> None:
    with _progress_lock:
        _progress_state[loc_name] = (pct, msg)
        _flush_progress()


# ── スクレイピング ────────────────────────────────────────────────────────

def get_available_times(page):
    today    = datetime.now(JST).date()
    tomorrow = today + timedelta(days=1)

    date_inputs = page.query_selector_all('input[name="dateTimeSelection"]')
    today_times, tomorrow_times = [], []

    for inp in date_inputs:
        val = inp.get_attribute('value')
        if not val:
            continue
        try:
            dt_jst = datetime.fromisoformat(val.replace('Z', '+00:00')).astimezone(JST)
        except Exception:
            continue

        date_jst = dt_jst.date()
        if date_jst not in (today, tomorrow):
            continue

        label = inp.evaluate_handle('el => el.closest("label")')
        svg   = label.query_selector('svg')
        if svg and 'rgb(0, 102, 255)' in svg.evaluate('el => getComputedStyle(el).fill'):
            t = dt_jst.strftime('%H:%M')
            if date_jst == today:
                today_times.append(t)
            else:
                tomorrow_times.append(t)

    return str(today), today_times, str(tomorrow), tomorrow_times


CDP_URL = "http://localhost:9222"


def scrape_location_worker(url: str) -> list[dict]:
    """1店舗分をCDP経由の既存ブラウザセッションでスクレイピングする。

    Playwright sync API はスレッド間で共有できないため、
    スレッドごとに sync_playwright() を生成し CDP に接続する。
    既存セッション（ログイン済み）を使うことでbot検出を回避する。
    """
    loc_name = url.split("/")[5]
    update_progress(loc_name, 5, f"{loc_name} 開始")

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP_URL)
        ctx  = browser.contexts[0]
        page = ctx.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_load_state("networkidle", timeout=10000)

            radio_btns = page.query_selector_all('input[type="radio"]')
            room_data: list[dict] = []

            for i, btn in enumerate(radio_btns):
                label     = btn.evaluate_handle('el => el.closest("label")')
                room_name = label.inner_text().split('\n')[0].strip()
                pct       = 10 + int((i / max(len(radio_btns), 1)) * 85)
                update_progress(loc_name, pct, f"{loc_name}/{room_name}")

                label.click()
                # networkidle fires before the calendar AJAX loads; wait for inputs to appear in DOM
                try:
                    page.wait_for_selector(
                        'input[name="dateTimeSelection"]', state="attached", timeout=15000
                    )
                except Exception:
                    pass  # no slots for this room

                today_str, today_times, tom_str, tom_times = get_available_times(page)
                for t in today_times:
                    room_data.append({"room": room_name, "date": today_str, "time": t})
                for t in tom_times:
                    room_data.append({"room": room_name, "date": tom_str,   "time": t})

        finally:
            page.close()

        update_progress(loc_name, 100, f"{loc_name} 完了")
        return room_data


# ── メイン ────────────────────────────────────────────────────────────────

def main() -> None:
    # 進捗状態を初期化
    for url in LOCATIONS:
        _progress_state[url.split("/")[5]] = (0, "")

    all_data: list[dict] = []
    errors:   list[str]  = []

    with ThreadPoolExecutor(max_workers=len(LOCATIONS)) as executor:
        future_to_url = {executor.submit(scrape_location_worker, url): url for url in LOCATIONS}
        for future in as_completed(future_to_url):
            url = future_to_url[future]
            try:
                all_data.extend(future.result())
            except Exception as exc:
                loc = url.split("/")[5]
                errors.append(f"{loc}: {exc}")
                update_progress(loc, 100, f"{loc} ERROR")

    result: dict = {"status": "done", "available_times": all_data}
    if errors:
        result["errors"] = errors

    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
