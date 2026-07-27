import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta

# mrs_browser ships as the "machine-readable-skills-browser" custom; a checkout
# at ~/work wins when present so a development copy still overrides it.
for _mrs_path in ("~/work/machine-readable-skills-browser",
                  "~/.mywant/custom-types/machine-readable-skills-browser"):
    _mrs_path = os.path.expanduser(_mrs_path)
    if os.path.isdir(_mrs_path):
        sys.path.insert(0, _mrs_path)
        break
from mrs_browser import browser_run  # noqa: E402

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

# 1店舗分: 各部屋(radio)を順にクリックし、クリック後に現れる日時候補
# (input[name="dateTimeSelection"]) のうち、closest(label) 内のsvgの
# computed fill色が青(rgb(0, 102, 255) = 空き)のものだけをvalue(ISO日時)
# として収集する。閉じたlabel/svgの取得はCSSでは表現できないancestor
# 探索が必要なため、xpath: ancestor::label[1] を使う（元のPlaywright実装の
# el.closest("label") と同じ意味）。svgはHTML内でSVG名前空間を持つため
# 無名前空間のxpathステップ "svg" では一致しない（既知のブラウザ挙動） —
# local-name() で名前空間を無視してマッチさせる。
ROOM_STEPS = [
    {"type": "waitForElement", "selectors": [['input[type="radio"]']], "timeout": 15000},
    {"type": "customStep", "name": "forEachClick", "parameters": {
        "selector": 'input[type="radio"]',
        "as": "rooms",
        "trigger_field": {"selector": "xpath:ancestor::label[1]", "extract": "text"},
        "trigger_key": "room",
        "wait_after_click": {"selector": 'input[name="dateTimeSelection"]', "timeout_ms": 15000},
        "read": {
            "selector": 'input[name="dateTimeSelection"]',
            "extract": "attr",
            "attr": "value",
            "filter": {
                "selector": "xpath:ancestor::label[1]//*[local-name()='svg']",
                "extract": "computedStyle",
                "style_prop": "fill",
                "contains": "rgb(0, 102, 255)",
            },
        },
    }},
]


def scrape_location_worker(url: str) -> list[dict]:
    """1店舗分を browser_run 経由でスクレイピングする（拡張が1タブで全部屋を巡回）。"""
    loc_name = url.split("/")[5]
    update_progress(loc_name, 5, f"{loc_name} 開始")

    today = datetime.now(JST).date()
    tomorrow = today + timedelta(days=1)

    # 部屋数が多い店舗は forEachClick の wait_after_click(部屋ごと最大15秒)が
    # 積み重なるため、拡張の1分ポーリング遅延も込みで余裕を持たせる。
    result = browser_run(url, ROOM_STEPS, timeout_ms=240000)
    rooms = result.get("rooms") or []

    room_data: list[dict] = []
    for entry in rooms:
        val = entry.get("value")
        room_name = (entry.get("room") or "").split("\n")[0].strip()
        if not val:
            continue
        try:
            dt_jst = datetime.fromisoformat(val.replace("Z", "+00:00")).astimezone(JST)
        except Exception:
            continue
        date_jst = dt_jst.date()
        if date_jst not in (today, tomorrow):
            continue
        room_data.append({"room": room_name, "date": str(date_jst), "time": dt_jst.strftime("%H:%M")})

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
