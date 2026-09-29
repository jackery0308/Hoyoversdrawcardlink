# HoYoverse Draw History Link

A small console app that grabs your draw (wish / warp / signal search) history link
from **Genshin Impact**, **Honkai: Star Rail** or **Zenless Zone Zero** on Windows.

When the history page opens in-game, the game's built-in browser calls a GET API
(`.../getGachaLog?authkey=...`) and caches it on disk. This tool reads that cache
and prints the latest link. It doesn't touch the game process or send your data anywhere.
The only network call is the optional `--validate` check, which goes to HoYoverse's own API.

## Requirements

- Windows with the game installed and started at least once
- Python 3.9+ (standard library only, nothing to `pip install`)

## Usage

Double-click `run.bat` for a menu, or use the command line:

```bat
:: Option A: open the history page in-game first, then run
python gacha_link.py genshin

:: Option B: run first, then open the history page (or go to the next page)
python gacha_link.py hsr watch

:: Extras
python gacha_link.py zzz --validate        :: call the API to check the authkey still works
python gacha_link.py genshin --clean       :: strip end_id/page so the link starts at page 1
python gacha_link.py genshin --path "D:\Games\Genshin Impact game"   :: skip log lookup
```

Games: `genshin`, `hsr`, `zzz` (global and CN clients).
The link is copied to your clipboard automatically (`--no-copy` turns this off).

## Steps in-game

1. Open the game and go to the draw screen (Wish / Warp / Signal Search).
2. Click **History** and let the page load. Clicking **next page** writes a fresh request too.
3. Run `fetch`. If you started `watch` before step 2, the link prints as soon as the page loads.

## Notes

- The `authkey` expires after about 24 hours. If `--validate` reports `authkey timeout`,
  open the history page again.
- The link works as a login token for your draw history, so don't share it publicly.

## Tests

```bash
python -m unittest discover -s tests -v
```
