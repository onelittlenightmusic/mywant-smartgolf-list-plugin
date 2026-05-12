import json
import sys
import time
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

def report_progress(percentage, message=""):
    print(json.dumps({"_progress": percentage, "_message": message}, ensure_ascii=False), flush=True)

def get_available_times(page):
    today = datetime.now(JST).date()
    tomorrow = today + timedelta(days=1)
    
    # Get all radio buttons for date-time
    date_inputs = page.query_selector_all('input[name="dateTimeSelection"]')
    today_times, tomorrow_times = [], []
    for inp in date_inputs:
        val = inp.get_attribute('value')
        if not val: continue
        try:
            dt_jst = datetime.fromisoformat(val.replace('Z', '+00:00')).astimezone(JST)
        except: continue
        
        date_jst = dt_jst.date()
        if date_jst not in (today, tomorrow): continue
        
        # Check if enabled (not filled)
        label = inp.evaluate_handle('el => el.closest("label")')
        svg = label.query_selector('svg')
        if svg and 'rgb(0, 102, 255)' in svg.evaluate('el => getComputedStyle(el).fill'):
            t = dt_jst.strftime('%H:%M')
            if date_jst == today: today_times.append(t)
            else: tomorrow_times.append(t)
            
    return str(today), today_times, str(tomorrow), tomorrow_times

def scrape_location(page, url, on_progress):
    page.goto(url, wait_until="domcontentloaded")
    time.sleep(2)
    
    # Radios are already on the page
    radio_btns = page.query_selector_all('input[type="radio"]')
    room_data = []
    
    for i, btn in enumerate(radio_btns):
        label = btn.evaluate_handle('el => el.closest("label")')
        room_name = label.inner_text().split('\n')[0].strip()
        on_progress(i, len(radio_btns), room_name)
        
        label.click()
        time.sleep(2)
        
        today_str, today_times, tom_str, tom_times = get_available_times(page)
        if today_times:
            room_data.append({"room": room_name, "date": today_str, "times": today_times})
        if tom_times:
            room_data.append({"room": room_name, "date": tom_str, "times": tom_times})
            
    return room_data

def main():
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://localhost:9222")
        page = browser.contexts[0].new_page()
        
        all_data = []
        for i, url in enumerate(LOCATIONS):
            loc_name = url.split("/")[5]
            def on_p(idx, total, name):
                pct = 10 + (i * 30) + int((idx/total)*30)
                report_progress(pct, f"{loc_name} {name}")
            
            all_data.extend(scrape_location(page, url, on_p))
            
        page.close()
        print(json.dumps({"status": "done", "available_times": all_data}, ensure_ascii=False))

if __name__ == "__main__":
    main()
