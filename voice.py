#!/usr/bin/env python3
"""スマートゴルフの空き一覧が言うこと。

- タイルに乗ったとき: いちばん早い空き。
- 案内（event.kind == "guide"）: 空き枠へ連れて行くあいだ、場面ごとの一言
  （main.py の goto が say_key で場面を、guide で事実を渡す）。
  {"say": {"choose_room": …, "find_slot": …, "arrived": …, "next": …}}

文はここで日本語で書く。キャラクタの言語への翻訳はサーバー（translate.go）が
ほかのセリフと同じようにする。実行のされ方は engine/core/want_voice.go を参照。
"""
import json
import re
import sys


def guide_lines(g: dict) -> dict:
    """場面ごとに、次に何をすればいいか。ロボットがしていることの実況では
    なく、見ている人への案内として言う。"""
    bay = g.get("bay") or "部屋"
    when = g.get("when") or ""
    # 題には部屋番号と時刻だけ: 「打席予約(Room02)」→「Room2」、「11/1 20:00」→「20:00」。
    m = re.search(r"Room\s*0*(\d+)", bay)
    room_no = f"Room{m.group(1)}" if m else bay
    clock = when.split(" ")[-1] if when else ""
    return {
        "choose_room": f"{bay} を選んでおくね",
        "find_slot": f"{when} の枠を探すから、ちょっと待ってね" if when else "空いてる枠を探すから、ちょっと待ってね",
        "arrived": "この時間でよければ、ここで決めて Cmd＋↓ で Next へ",
        "next": "ここで A（Enter）を押すと予約確認へ。まだ予約は確定しないよ",
        "agree": "内容を確かめたら、ここで規約に同意してチェック。Cmd＋↓ で確定ボタンへ",
        "confirm": "ここを押すと予約が確定するよ。よければ A（Enter）で決定",
        # 進捗の吹き出しの題と、そこに並ぶ各場面の短い名前（"title:" + 場面）。
        "title": f"{clock} の {room_no} を予約しよう" if clock else f"{room_no} を予約しよう",
        "title:choose_room": f"{room_no} を選ぶ",
        "title:find_slot": f"{clock} の枠を探す" if clock else "空き枠を探す",
        "title:arrived": "時間を決める",
        "title:next": "Next へ進む",
        "title:agree": "規約に同意する",
        "title:confirm": "予約を確定する",
    }


def first_open(state: dict):
    slots = state.get("smartgolf_all_available_times") or []
    if not isinstance(slots, list) or not slots:
        return None
    s = slots[0]
    store = s.get("store") or ""
    time = s.get("time") or ""
    return f"{store}の{time}が空いてるよ" if store and time else None


def main() -> None:
    data = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    event = data.get("event") or {}
    if event.get("kind") == "guide":
        print(json.dumps({"say": guide_lines(event.get("guide") or {})}, ensure_ascii=False))
        return
    print(json.dumps({"say": first_open(data.get("state") or {})}, ensure_ascii=False))


if __name__ == "__main__":
    main()
