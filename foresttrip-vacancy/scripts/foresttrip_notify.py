#!/usr/bin/env python3
"""Notification helpers for foresttrip-vacancy watcher."""
from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request
from typing import Any


# ---------------------------------------------------------------------------
# macOS notification
# ---------------------------------------------------------------------------

def notify_mac(title: str, body: str, *, sound: str = "Ping") -> None:
    script = (
        f'display notification "{body}" with title "{title}" sound name "{sound}"'
    )
    subprocess.run(["osascript", "-e", script], check=False)


# ---------------------------------------------------------------------------
# Slack webhook
# ---------------------------------------------------------------------------

def notify_slack(message: str, webhook_url: str) -> bool:
    payload = json.dumps({"text": message}).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError):
        return False


# ---------------------------------------------------------------------------
# KakaoTalk (macOS app via osascript)
# ---------------------------------------------------------------------------

_KAKAO_SEND_SCRIPT = """\
set msgText to {message!r}
tell application "KakaoTalk"
    activate
end tell
delay 0.8
tell application "System Events"
    tell process "KakaoTalk"
        set frontmost to true
        keystroke "f" using command down
        delay 0.5
        keystroke {recipient!r}
        delay 1.2
        key code 36
        delay 0.8
        keystroke msgText
        delay 0.3
        key code 36
    end tell
end tell
"""


def notify_kakao(message: str, recipient: str = "나에게 보내기") -> bool:
    """Send a KakaoTalk message by GUI-automating the macOS app."""
    script = _KAKAO_SEND_SCRIPT.format(message=message, recipient=recipient)
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    return result.returncode == 0


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

def send_all(message: str, config: dict[str, Any]) -> None:
    """Send notifications to all enabled channels from watchlist config."""
    notify_section = config.get("notify", {})

    # macOS notification (always on)
    notify_mac("자연휴양림 예약", message)

    # Slack
    slack = notify_section.get("slack", {})
    if slack.get("enabled") and slack.get("webhook_url"):
        notify_slack(message, slack["webhook_url"])

    # KakaoTalk
    kakao = notify_section.get("kakao", {})
    if kakao.get("enabled", True):
        recipient = kakao.get("recipient", "나에게 보내기")
        notify_kakao(message, recipient)
