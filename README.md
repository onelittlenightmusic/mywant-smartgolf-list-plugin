# mywant-smartgolf-list-plugin

MyWant custom type plugin for listing available SmartGolf booking times across all stores (北新宿/中野新橋/新中野).

## Installation

```bash
cd ~/.mywant/custom-types
git clone https://github.com/onelittlenightmusic/mywant-smartgolf-list-plugin
```

## Usage

```yaml
metadata:
  name: check_smartgolf
  type: smartgolf_list_available
```

## Requirements

- Python 3, Playwright, Chrome with remote debugging (port 9222)
