import json
import sys
import time
from datetime import datetime, timezone, timedelta

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print(json.dumps({
        "error": "playwright module not found. Install with: pip3 install playwright && playwright install chromium"
    }, ensure_ascii=False))
    sys.exit(1)

JST = timezone(timedelta(hours=9))

LOCATIONS = [
    "https://smartgolf.stores.jp/reserve/smartgolf_kitashinjuku/3421038/book/course_type",
    "https://smartgolf.stores.jp/reserve/smartgolf_nakanoshimbashi/1459178/book/course_type",
    "https://smartgolf.stores.jp/reserve/smartgolf_shinnakano/4619269/book/course_type",
]


def report_progress(percentage, message=""):
    print(json.dumps({"_progress": percentage, "_message": message}, ensure_ascii=False), flush=True)


def get_available_times(page):
    """今日・明日の空き時間をJSTで取得"""
    today = datetime.now(JST).date()
    tomorrow = today + timedelta(days=1)

    date_inputs = page.query_selector_all('input[name="dateTimeSelection"]')
    today_times, tomorrow_times = [], []

    for inp in date_inputs:
        val = inp.get_attribute('value')
        if not val:
            continue
        try:
            dt_jst = datetime.fromisoformat(val.replace('Z', '+00:00')).astimezone(JST)
        except ValueError:
            continue

        date_jst = dt_jst.date()
        if date_jst not in (today, tomorrow):
            continue

        label = inp.evaluate_handle('el => el.closest("label")')
        if not label:
            continue
        content = label.query_selector('[class*="GridCellInput_content__"]')
        if not content:
            continue
        svg = content.query_selector('svg')
        if not svg:
            continue
        fill = svg.evaluate('el => getComputedStyle(el).fill')
        if 'rgb(0, 102, 255)' not in fill:
            continue

        t = dt_jst.strftime('%H:%M')
        if date_jst == today:
            today_times.append(t)
        else:
            tomorrow_times.append(t)

    return str(today), today_times, str(tomorrow), tomorrow_times


def scrape_location(page, url, on_room_progress=None):
    """1店舗分の全部屋の空き時間を取得して返す"""
    page.goto(url, wait_until="domcontentloaded")
    time.sleep(3)

    select_btn = page.query_selector('[class*="CourseSelectModal"]')
    if not select_btn:
        return None, "Service select button not found"

    page.evaluate('btn => btn.click()', select_btn)
    time.sleep(1)

    modal = page.query_selector('[class*="RSModal_content"]')
    if not modal:
        return None, "Service selection modal not found"

    radio_inputs = modal.query_selector_all('input[type="radio"]')
    if not radio_inputs:
        return None, "No rooms found in modal"

    # 部屋名を取得してモーダルを閉じる
    room_names = []
    for inp in radio_inputs:
        label = inp.evaluate_handle('el => el.closest("label")')
        bold = label.query_selector('.font-bold')
        name = bold.inner_text().strip() if bold else f"Room{len(room_names)+1}"
        room_names.append(name)

    close_btn = modal.query_selector('button')  # 最初のボタンはclose
    page.evaluate('btn => btn.click()', close_btn)
    time.sleep(0.5)

    # 各部屋の空き時間を取得
    results = []
    for room_idx, room_name in enumerate(room_names):
        if on_room_progress:
            on_room_progress(room_idx, len(room_names), room_name)

        select_btn = page.query_selector('[class*="CourseSelectModal"]')
        page.evaluate('btn => btn.click()', select_btn)
        time.sleep(1)

        modal = page.query_selector('[class*="RSModal_content"]')
        radio_inputs = modal.query_selector_all('input[type="radio"]')

        page.evaluate('inp => inp.closest("label").click()', radio_inputs[room_idx])
        time.sleep(0.3)

        ok_btn = modal.query_selector('button:has-text("OK")')
        page.evaluate('btn => btn.click()', ok_btn)
        time.sleep(2)

        today_str, today_times, tomorrow_str, tomorrow_times = get_available_times(page)

        if today_times:
            results.append({
                "room": room_name,
                "date": today_str,
                "times": [{"number": i + 1, "time": t} for i, t in enumerate(today_times)],
            })
        if tomorrow_times:
            results.append({
                "room": room_name,
                "date": tomorrow_str,
                "times": [{"number": i + 1, "time": t} for i, t in enumerate(tomorrow_times)],
            })

    return results, None


def main():
    try:
        with sync_playwright() as p:
            report_progress(5, "Connecting to browser")
            browser = p.chromium.connect_over_cdp("http://localhost:9222")
            context = browser.contexts[0]
            page = context.new_page()
            report_progress(10, "Connected")

            all_available = []
            errors = []
            total_locations = len(LOCATIONS)
            # 各店舗に10〜90%の範囲を均等割り当て
            pct_per_location = 80 // total_locations

            for loc_idx, url in enumerate(LOCATIONS):
                location_name = url.split("/")[5].replace("smartgolf_", "").replace("_", " ")
                pct_base = 10 + loc_idx * pct_per_location
                report_progress(pct_base, f"Scanning {location_name} ({loc_idx + 1}/{total_locations})")

                def on_room(room_idx, room_total, room_name, _base=pct_base, _span=pct_per_location):
                    pct = _base + int((room_idx / room_total) * _span)
                    report_progress(pct, f"{location_name} - {room_name} ({room_idx + 1}/{room_total})")

                results, err = scrape_location(page, url, on_room_progress=on_room)
                if err:
                    errors.append({"url": url, "error": err})
                else:
                    all_available.extend(results)

            report_progress(92, "Finalizing results")
            page.close()

            # Flatten results: [{room, date, times: [...]}] -> [{room, date, time}]
            flattened = []
            for entry in all_available:
                room = entry.get("room")
                date = entry.get("date")
                for t in entry.get("times", []):
                    flattened.append({
                        "room": room,
                        "date": date,
                        "time": t.get("time")
                    })

            flattened.sort(key=lambda x: (x["date"], x["time"], x["room"]))
            output = {"status": "done", "available_times": flattened}
            if errors:
                output["errors"] = errors

            report_progress(100, "Done")
            print(json.dumps(output, ensure_ascii=False), flush=True)

    except Exception as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False), flush=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
