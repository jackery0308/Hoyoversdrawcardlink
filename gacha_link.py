#!/usr/bin/env python3
"""
HoYoverse gacha (wish / warp / signal search) history link fetcher.

How it works
------------
When you open the draw history page in-game, the game's embedded browser
calls a GET API such as

    https://public-operation-hk4e-sg.hoyoverse.com/gacha_info/api/getGachaLog?authkey=...

and caches that request in its web cache file (``webCaches/<ver>/Cache/Cache_Data/data_2``).
This tool:

1. Reads the game's log file to find where the game is installed.
2. Opens the newest web cache file.
3. Pulls out the most recent draw-history URL (with its ``authkey``).
4. Optionally checks the link against the API and copies it to the clipboard.

Two ways to use it:

* ``fetch`` - open the history page in-game first, then run this.
* ``watch`` - run this first, then open the history page (or flip to the next
  page); the link is printed as soon as the game writes it.

Only the Python standard library is used. Run ``python gacha_link.py -h``.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional


@dataclass(frozen=True)
class Game:
    key: str
    name: str
    # Log files relative to %USERPROFILE%\AppData\LocalLow (global + CN clients).
    logs: tuple[str, ...]
    # Name of the "<Game>_Data" folder that appears inside log lines.
    data_dirs: tuple[str, ...]


GAMES: dict[str, Game] = {
    "genshin": Game(
        key="genshin",
        name="Genshin Impact",
        logs=(
            "miHoYo/Genshin Impact/output_log.txt",
            "miHoYo/Genshin Impact/output_log.txt.last",
            "miHoYo/原神/output_log.txt",
            "miHoYo/原神/output_log.txt.last",
        ),
        data_dirs=("GenshinImpact_Data", "YuanShen_Data"),
    ),
    "hsr": Game(
        key="hsr",
        name="Honkai: Star Rail",
        logs=(
            "Cognosphere/Star Rail/Player.log",
            "Cognosphere/Star Rail/Player-prev.log",
            "miHoYo/崩坏：星穹铁道/Player.log",
            "miHoYo/崩坏：星穹铁道/Player-prev.log",
        ),
        data_dirs=("StarRail_Data",),
    ),
    "zzz": Game(
        key="zzz",
        name="Zenless Zone Zero",
        logs=(
            "miHoYo/ZenlessZoneZero/Player.log",
            "miHoYo/ZenlessZoneZero/Player-prev.log",
            "miHoYo/绝区零/Player.log",
            "miHoYo/绝区零/Player-prev.log",
        ),
        data_dirs=("ZenlessZoneZero_Data",),
    ),
}

# Printable ASCII run starting with https:// - URLs in the Chromium cache are
# stored as plain bytes terminated by a NUL / binary byte.
_URL_RE = re.compile(rb"https://[\x21-\x7e]+")
_GACHA_MARKERS = ("getGachaLog", "getLdGachaLog", "gacha")


class LinkError(Exception):
    """A user-facing error (printed without a traceback)."""


# --------------------------------------------------------------------------- #
# Locating the game
# --------------------------------------------------------------------------- #
def locallow_dir() -> Path:
    profile = os.environ.get("USERPROFILE") or str(Path.home())
    return Path(profile) / "AppData" / "LocalLow"


def find_data_dir_in_log(text: str, game: Game) -> Optional[Path]:
    """Return the last ``<Game>_Data`` directory mentioned in a log file."""
    names = "|".join(re.escape(d) for d in game.data_dirs)
    pattern = re.compile(r"([A-Za-z]:[\\/][^\r\n:*?\"<>|]*?(?:%s))(?=[\\/]|$|\s)" % names)
    matches = pattern.findall(text)
    for raw in reversed(matches):
        path = Path(raw.replace("\\", "/"))
        if path.is_dir():
            return path
    return Path(matches[-1].replace("\\", "/")) if matches else None


def locate_data_dir(game: Game) -> Path:
    base = locallow_dir()
    tried = []
    for rel in game.logs:
        log = base / rel
        tried.append(str(log))
        if not log.is_file():
            continue
        text = log.read_text(encoding="utf-8", errors="ignore")
        found = find_data_dir_in_log(text, game)
        if found is not None:
            return found
    raise LinkError(
        f"Could not find the {game.name} install folder from its log file.\n"
        "Start the game at least once, or pass the folder with --path.\n"
        "Looked in:\n  " + "\n  ".join(tried)
    )


def resolve_data_dir(path: Path, game: Game) -> Path:
    """Accept either the <Game>_Data folder or the folder that contains it."""
    if path.name in game.data_dirs or (path / "webCaches").is_dir():
        return path
    for name in game.data_dirs:
        if (path / name).is_dir():
            return path / name
    return path


def find_cache_file(data_dir: Path) -> Path:
    """Newest ``data_2`` under ``webCaches`` (versioned folders or legacy layout)."""
    web = data_dir / "webCaches"
    candidates = list(web.glob("*/Cache/Cache_Data/data_2"))
    candidates += list(web.glob("Cache/Cache_Data/data_2"))
    candidates = [c for c in candidates if c.is_file()]
    if not candidates:
        raise LinkError(
            f"No web cache found under:\n  {web}\n"
            "Open the draw history page in-game at least once."
        )
    return max(candidates, key=lambda p: p.stat().st_mtime)


# --------------------------------------------------------------------------- #
# Extracting the link
# --------------------------------------------------------------------------- #
def read_bytes(path: Path) -> bytes:
    """Read the cache file; fall back to a temp copy if the game holds a lock."""
    try:
        return path.read_bytes()
    except PermissionError:
        tmp = Path(os.environ.get("TEMP", "/tmp")) / "gacha_link_data_2.tmp"
        try:
            shutil.copyfile(path, tmp)
            return tmp.read_bytes()
        except OSError as exc:
            raise LinkError(f"Cannot read {path}: {exc}. Try closing the game.") from exc
        finally:
            tmp.unlink(missing_ok=True)


def extract_gacha_urls(blob: bytes) -> list[str]:
    """All draw-history URLs in the blob, in file order (oldest first)."""
    urls = []
    for m in _URL_RE.finditer(blob):
        url = m.group().decode("ascii")
        if "authkey=" in url and any(k in url for k in _GACHA_MARKERS):
            urls.append(url)
    return urls


def pick_latest(urls: Iterable[str]) -> Optional[str]:
    """Prefer the last API call (getGachaLog); fall back to the last page URL."""
    urls = list(urls)
    api = [u for u in urls if "GachaLog" in u]
    if api:
        return api[-1]
    return urls[-1] if urls else None


def clean_url(url: str) -> str:
    """Drop paging params so the link starts from the first page."""
    parts = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    drop = {"end_id", "begin_id", "page"}
    query = [(k, v) for k, v in query if k not in drop]
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query, safe="/=")))


def latest_link(cache_file: Path) -> Optional[str]:
    return pick_latest(extract_gacha_urls(read_bytes(cache_file)))


# --------------------------------------------------------------------------- #
# Validation / clipboard
# --------------------------------------------------------------------------- #
def validate(url: str, timeout: float = 10.0) -> tuple[bool, str]:
    """Call the GET API and report whether the authkey is still accepted."""
    if "GachaLog" not in url:
        return True, "not an API URL, skipped online check"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        return False, f"request failed: {exc}"
    code = body.get("retcode")
    if code == 0:
        return True, "OK"
    return False, f"retcode {code}: {body.get('message')}"


def copy_to_clipboard(text: str) -> bool:
    commands = [["clip"], ["pbcopy"], ["wl-copy"], ["xclip", "-selection", "clipboard"]]
    for cmd in commands:
        if shutil.which(cmd[0]):
            try:
                subprocess.run(cmd, input=text.encode("utf-8"), check=True)
                return True
            except (OSError, subprocess.CalledProcessError):
                continue
    return False


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
def get_data_dir(game: Game, path: Optional[str]) -> Path:
    data_dir = resolve_data_dir(Path(path), game) if path else locate_data_dir(game)
    print(f"Game data folder : {data_dir}")
    return data_dir


def report(url: str, args: argparse.Namespace) -> None:
    if args.clean:
        url = clean_url(url)
    print("\nDraw history link:\n")
    print(url)
    print()
    if args.validate:
        ok, msg = validate(url)
        print(f"API check        : {'valid' if ok else 'INVALID'} ({msg})")
    if not args.no_copy:
        print("Clipboard        : " + ("copied" if copy_to_clipboard(url) else "unavailable"))


def cmd_fetch(game: Game, args: argparse.Namespace) -> int:
    cache = find_cache_file(get_data_dir(game, args.path))
    print(f"Web cache file   : {cache}")
    url = latest_link(cache)
    if not url:
        raise LinkError(
            "No draw history link in the cache yet.\n"
            "Open the history page in-game, then run again (or use 'watch')."
        )
    report(url, args)
    return 0


def cmd_watch(game: Game, args: argparse.Namespace) -> int:
    data_dir = get_data_dir(game, args.path)
    try:
        baseline = latest_link(find_cache_file(data_dir))
    except LinkError:
        baseline = None  # no cache yet; the first link that appears is new
    print(f"\nWaiting for you to open the {game.name} history page "
          f"(or the next page)... Ctrl+C to stop.")
    deadline = time.monotonic() + args.timeout if args.timeout else None
    try:
        while deadline is None or time.monotonic() < deadline:
            time.sleep(args.interval)
            try:
                # Re-resolve each time: a game update may add a new webCaches folder.
                url = latest_link(find_cache_file(data_dir))
            except LinkError:
                continue
            if url and url != baseline:
                report(url, args)
                return 0
    except KeyboardInterrupt:
        print("\nStopped.")
        return 1
    raise LinkError("Timed out waiting for a new link.")


def interactive() -> list[str]:
    """Double-click friendly menu used when no arguments are given."""
    print("HoYoverse draw history link fetcher\n")
    keys = list(GAMES)
    for i, key in enumerate(keys, 1):
        print(f"  {i}. {GAMES[key].name}")
    choice = input("\nSelect game [1]: ").strip() or "1"
    game = keys[int(choice) - 1] if choice.isdigit() and 0 < int(choice) <= len(keys) else "genshin"
    print("\n  1. I already opened the history page -> fetch now")
    print("  2. Start watching, then I'll open the history page")
    mode = input("\nSelect mode [1]: ").strip() or "1"
    return [game, "watch" if mode == "2" else "fetch", "--validate"]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Fetch the HoYoverse draw (gacha) history link from the game's web cache.")
    p.add_argument("game", choices=sorted(GAMES), help="genshin | hsr | zzz")
    p.add_argument("mode", nargs="?", default="fetch", choices=["fetch", "watch"],
                   help="fetch: read now (default). watch: wait until you open the page.")
    p.add_argument("--path", help="Game install folder or <Game>_Data folder (skip log lookup)")
    p.add_argument("--validate", action="store_true", help="Call the API to check the authkey")
    p.add_argument("--clean", action="store_true", help="Strip paging params (end_id, page)")
    p.add_argument("--no-copy", action="store_true", help="Do not copy to the clipboard")
    p.add_argument("--interval", type=float, default=1.5, help="watch poll interval, seconds")
    p.add_argument("--timeout", type=float, default=0, help="watch timeout, seconds (0 = none)")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    pause = not argv  # keep the window open when launched by double-click
    if not argv:
        argv = interactive()
    args = build_parser().parse_args(argv)
    game = GAMES[args.game]
    try:
        return cmd_watch(game, args) if args.mode == "watch" else cmd_fetch(game, args)
    except LinkError as exc:
        print(f"\nError: {exc}", file=sys.stderr)
        return 1
    finally:
        if pause:
            input("\nPress Enter to exit...")


if __name__ == "__main__":
    sys.exit(main())
