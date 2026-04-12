---
name: mywant-smartgolf-list-plugin
description: 北新宿・中野新橋・新中野スマートゴルフの全部屋の今日・明日の予約可能な時間帯をJSON形式で取得する。空き時間の確認・一覧表示が必要なときに使用する。Playwright経由でChromeのCDPに接続する。
compatibility:
  python: ">=3.10"
  requires:
    - playwright (sync_api)
    - Chrome with remote debugging on port 9222
metadata:
  output-format: json
  json-schema: see "出力JSON形式" section below
---

## 使い方

```bash
python3 "${CLAUDE_SKILL_DIR}/main.py"
```

引数なし。Chromeのリモートデバッグ（ポート9222）が有効になっている必要がある。

## 出力JSON形式

```json
{
  "available_times": [
    {
      "room": "中野新橋店/打席予約(Room02)",
      "date": "2026-04-12",
      "time": "20:00"
    },
    {
      "room": "新中野店/打席予約(Room01)",
      "date": "2026-04-12",
      "time": "21:00"
    }
  ]
}
```

### フィールド説明

| フィールド | 型 | 説明 |
|---|---|---|
| `available_times` | array | 予約可能な時間帯のリスト（日時・部屋でソート済み） |
| `available_times[n].room` | string | 部屋名（例: "中野新橋店/打席予約(Room02)"） |
| `available_times[n].date` | string | 日付（YYYY-MM-DD形式） |
| `available_times[n].time` | string | 時刻（HH:MM形式、JST） |
| `errors` | array | 取得に失敗した店舗の情報（任意） |

### エラー時

```json
{ "error": "エラーの説明文" }
```
