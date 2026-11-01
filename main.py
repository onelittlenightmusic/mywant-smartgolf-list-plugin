import json
import os
import sys
import threading
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

from concurrent.futures import ThreadPoolExecutor, as_completed  # noqa: E402

try:
    from mywant import parallel  # noqa: E402
except ImportError:
    # SDK のない実行環境では同じ読み方をここで（MYWANT_PARALLEL、未設定なら全部）。
    def parallel(default: int) -> int:
        raw = os.environ.get("MYWANT_PARALLEL", "").strip()
        try:
            return max(1, min(int(raw), default)) if raw else max(1, default)
        except ValueError:
            return max(1, default)

LOCATIONS = [
    "https://smartgolf.stores.jp/reserve/smartgolf_kitashinjuku/3421038/book/course_type",
    "https://smartgolf.stores.jp/reserve/smartgolf_nakanoshimbashi/1459178/book/course_type",
    "https://smartgolf.stores.jp/reserve/smartgolf_shinnakano/4619269/book/course_type",
    "https://smartgolf.stores.jp/reserve/smartgolf_nakano-yubinkyokumae/1879039/book/course_type",
    "https://smartgolf.stores.jp/reserve/smartgolf_hatsudai/3742491/book/course_type",
]

# 何店同時に読むか（上の parallel、サーバーが決める）。
WORKERS = parallel(len(LOCATIONS))

# 店の呼び名。空き枠ごとに store として載せ、カードはこれで店を並べる。
# 知らない店は URL の店名部分をそのまま使う。
STORE_NAMES = {
    "smartgolf_kitashinjuku": "北新宿店",
    "smartgolf_nakanoshimbashi": "中野新橋店",
    "smartgolf_shinnakano": "新中野店",
    "smartgolf_nakano-yubinkyokumae": "中野郵便局前店",
    "smartgolf_hatsudai": "初台店",
}

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
        # until_changed: 部屋を切り替えても前の部屋の表がしばらく残るので、
        # 出ていることだけを待つと前の部屋の空きを読んでしまう（Room02 に
        # Room01 の空きが入っていた）。表が変わって落ち着くまで待つ。
        "wait_after_click": {"selector": 'input[name="dateTimeSelection"]', "timeout_ms": 15000,
                             "until_changed": True},
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


# 空き枠へ連れて行く手順の形は goto.json（SDK の取り決め、mywant/guide.py）。
# 部屋や時刻で変わるところだけが {{…}}。形は答えに一度だけ（goto_template）、
# 枠には埋める値（goto_values）だけを持たせ、押されたとき GUI が埋める。
# 枠ごとに丸ごと入れると、百数十枠 × 履歴の分だけ同じ手順が state に積もり、
# 小さいサーバーのバックエンドが state の書き出しで落ちた（fly、2026-11-01）。
try:
    from mywant import goto_template  # noqa: E402
except ImportError:
    # SDK のない実行環境（ローカルの Docker runner など）では同じことをここで。
    def _strip(t):
        if isinstance(t, list):
            return [_strip(i) for i in t]
        if isinstance(t, dict):
            return {k: _strip(i) for k, i in t.items() if not k.startswith("_")}
        return t

    _GOTO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "goto.json")

    def goto_template() -> dict:
        with open(_GOTO, encoding="utf-8") as f:
            return _strip(json.load(f))


def goto_values(room: str, value: str, name: str, when: str = "") -> dict:
    """予約ページのこの枠へ連れて行く手順を goto.json から作るための値。"""
    bay = room.split("/")[-1] if "/" in room else room
    return {"room": room, "bay": bay, "value": value, "when": when, "name": name}


def scrape_location_worker(url: str) -> list[dict]:
    """1店舗分を browser_run 経由でスクレイピングする（拡張が1タブで全部屋を巡回）。"""
    loc_name = url.split("/")[5]
    update_progress(loc_name, 5, f"{loc_name} 開始")

    today = datetime.now(JST).date()
    tomorrow = today + timedelta(days=1)

    # 部屋数が多い店舗は forEachClick の wait_after_click(部屋ごと最大15秒)が
    # 積み重なるため、拡張の1分ポーリング遅延も込みで余裕を持たせる。
    # quiet: 空き状況の確認はユーザーの行動を妨げない。ログイン切れなどでも
    # 通知を出さず、タブも残さない。
    # 一店ずつ読むときは、一店にかけられるのは全体（skill_timeout_seconds）の五分の一まで。
    result = browser_run(url, ROOM_STEPS, timeout_ms=240000 if WORKERS > 1 else 110000, quiet=True)
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
        # store と url: どの店のどの枠かを、カードが店ごとに並べて予約ページへ
        # 飛ばせるように。url は店の予約ページ（枠そのものへ直接入るリンクは
        # サイトにないので、店の入口）。
        room_data.append({
            "room": room_name,
            "date": str(date_jst),
            "time": dt_jst.strftime("%H:%M"),
            "store": STORE_NAMES.get(url.split("/")[4], room_name.split("/")[0] or loc_name),
            "url": url,
            "goto_values": goto_values(room_name, val, f"{dt_jst.month}/{dt_jst.day} {dt_jst.strftime('%H:%M')} {room_name}",
                                       f"{dt_jst.month}/{dt_jst.day} {dt_jst.strftime('%H:%M')}"),
        })

    update_progress(loc_name, 100, f"{loc_name} 完了")
    return room_data


# ── メイン ────────────────────────────────────────────────────────────────

def main() -> None:
    # 進捗状態を初期化
    for url in LOCATIONS:
        _progress_state[url.split("/")[5]] = (0, "")

    all_data: list[dict] = []
    errors:   list[str]  = []

    # 何店同時に読むかはサーバー次第（MYWANT_PARALLEL、SDK の parallel）。
    # 何も言われなければ全店同時。小さいサーバー（fly の 512MB では 2026-11-01、
    # 5店同時の待ちが重なってバックエンドが落ちた）は 1 と言い、一店ずつになる。
    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        future_to_url = {executor.submit(scrape_location_worker, url): url for url in LOCATIONS}
        for future in as_completed(future_to_url):
            url = future_to_url[future]
            try:
                all_data.extend(future.result())
            except Exception as exc:
                loc = url.split("/")[5]
                errors.append(f"{loc}: {exc}")
                update_progress(loc, 100, f"{loc} ERROR")

    # 時刻順に。店ごとの並列取得は終わった順に混ざるので、ここで並べ直す。
    all_data.sort(key=lambda d: (d["date"], d["time"], d["store"], d["room"]))
    result: dict = {"status": "done", "available_times": all_data, "goto_template": goto_template()}
    if errors:
        result["errors"] = errors

    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
