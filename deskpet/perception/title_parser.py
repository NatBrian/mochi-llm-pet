"""Window title -> coarse content guess. Never stores raw page text."""

from __future__ import annotations

import re

_BROWSERS = {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe"}
_EDITORS = {"code.exe", "devenv.exe", "pycharm64.exe", "sublime_text.exe", "notepad++.exe"}
_MEDIA = {"vlc.exe", "wmplayer.exe", "spotify.exe", "mpc-hc64.exe"}
_TERMINALS = {"windowsterminal.exe", "cmd.exe", "powershell.exe", "wt.exe"}

_VIDEO_SITES = re.compile(r"\b(youtube|netflix|twitch|bilibili|vimeo|hulu|disney\+)\b", re.I)
_SOCIAL = re.compile(r"\b(twitter|x\.com|reddit|instagram|facebook|tiktok)\b", re.I)


def content_guess(process: str, title: str) -> str | None:
    p = (process or "").lower()
    t = title or ""
    if p in _BROWSERS:
        if _VIDEO_SITES.search(t):
            return "youtube" if re.search(r"youtube", t, re.I) else "video"
        if _SOCIAL.search(t):
            return "social"
        return "web"
    if p in _EDITORS:
        return "code"
    if p in _MEDIA:
        return "media"
    if p in _TERMINALS:
        return "terminal"
    return None
