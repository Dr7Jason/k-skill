#!/usr/bin/env python3
"""
Watcher: polls foresttrip.go.kr vacancy endpoint, auto-books on match.

Usage:
    python3 run_foresttrip_watcher.py --watchlist ~/.config/k-skill/foresttrip-watchlist.yaml
    python3 run_foresttrip_watcher.py --watchlist ./config/watchlist-example.yaml --dry-run

Env vars required:
    KSKILL_FORESTTRIP_ID
    KSKILL_FORESTTRIP_PASSWORD
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import random
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import yaml  # PyYAML
except ImportError:
    raise SystemExit("PyYAML required: pip install pyyaml")

SCRIPT_DIR = Path(__file__).parent
VACANCY_SCRIPT = SCRIPT_DIR / "run_foresttrip_vacancy.py"
BOOK_SCRIPT = SCRIPT_DIR / "run_foresttrip_book.py"
NOTIFY_MODULE = SCRIPT_DIR / "foresttrip_notify.py"

LOG_DIR = Path("~/.cache/k-skill/foresttrip-vacancy/logs").expanduser()
STATUS_FILE = Path("~/.cache/k-skill/foresttrip-vacancy/watcher-status.json").expanduser()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("foresttrip-watcher")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class Target:
    forest_name: str = ""
    forest_id: str = ""
    dates: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=lambda: ["01", "02"])
    room_keywords: list[str] = field(default_factory=list)
    max_capacity: int | None = None

    # runtime
    done: bool = False
    booked_date: str = ""
    booked_room: str = ""


@dataclass
class WatcherConfig:
    targets: list[Target]
    booker_name: str
    booker_phone: str
    people: int
    payment_method: str = "bank_transfer"
    poll_interval: int = 180
    poll_jitter: int = 30
    max_book_attempts: int = 3
    notify: dict[str, Any] = field(default_factory=dict)
    session_cache: str = "~/.cache/k-skill/foresttrip-vacancy/session.json"
    headless: bool = True


def load_config(path: Path) -> WatcherConfig:
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    targets = [
        Target(
            forest_name=t.get("forest_name", ""),
            forest_id=t.get("forest_id", ""),
            dates=[str(d) for d in t.get("dates", [])],
            categories=[str(c) for c in t.get("categories", ["01", "02"])],
            room_keywords=t.get("room_keywords", []),
            max_capacity=t.get("max_capacity"),
        )
        for t in raw.get("targets", [])
    ]

    booker = raw.get("booker", {})
    poll = raw.get("poll", {})

    return WatcherConfig(
        targets=targets,
        booker_name=booker.get("name", ""),
        booker_phone=booker.get("phone", ""),
        people=int(booker.get("people", 2)),
        payment_method=booker.get("payment_method", "bank_transfer"),
        poll_interval=int(poll.get("interval_seconds", 180)),
        poll_jitter=int(poll.get("jitter_seconds", 30)),
        max_book_attempts=int(poll.get("max_book_attempts", 3)),
        notify=raw.get("notify", {}),
        session_cache=raw.get("session_cache", "~/.cache/k-skill/foresttrip-vacancy/session.json"),
        headless=bool(raw.get("headless", True)),
    )


# ---------------------------------------------------------------------------
# Vacancy check (calls existing script as subprocess)
# ---------------------------------------------------------------------------

def check_vacancies(target: Target, session_cache: str) -> list[dict[str, Any]]:
    """Call run_foresttrip_vacancy.py and return list of matching vacancy rows."""
    if not target.dates:
        return []

    dates_arg = ",".join(target.dates)
    cats_arg = ",".join(target.categories)

    cmd = [
        sys.executable, str(VACANCY_SCRIPT),
        "--json",
        "--dates", dates_arg,
        "--categories", cats_arg,
        "--session-cache", session_cache,
    ]
    if target.forest_id:
        cmd.extend(["--forest-id", target.forest_id])
    elif target.forest_name:
        cmd.extend(["--forest-name", target.forest_name])
    else:
        cmd.append("--all")

    env = dict(os.environ)
    result = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=120)

    if result.returncode not in (0, 1):
        log.warning("vacancy script exited %d: %s", result.returncode, result.stderr[:300])
        return []

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        log.warning("vacancy script returned non-JSON: %s", result.stdout[:200])
        return []

    hits: list[dict[str, Any]] = []
    for forest in payload.get("results", []):
        for date_group in forest.get("dates", []):
            for room in date_group.get("rooms", []):
                hits.append({
                    "forest": forest.get("forest", ""),
                    "forest_id": room.get("forest_id", target.forest_id),
                    "date": date_group.get("use_dt", ""),
                    "room_name": room.get("name", ""),
                    "category": room.get("category", ""),
                    "capacity": room.get("capacity"),
                })
    return hits


def filter_hits(hits: list[dict[str, Any]], target: Target) -> list[dict[str, Any]]:
    """Filter vacancy rows by target criteria."""
    out = []
    for h in hits:
        if target.room_keywords and not any(kw in h["room_name"] for kw in target.room_keywords):
            continue
        cap = h.get("capacity")
        if target.max_capacity and cap and cap > target.max_capacity:
            continue
        out.append(h)
    return out


# ---------------------------------------------------------------------------
# Booking
# ---------------------------------------------------------------------------

def attempt_booking(hit: dict[str, Any], cfg: WatcherConfig, dry_run: bool) -> dict[str, Any]:
    if dry_run:
        log.info("[DRY-RUN] would book forest=%s date=%s room=%s",
                 hit["forest"], hit["date"], hit["room_name"])
        return {"success": True, "dry_run": True, "reservation_no": "DRY-RUN"}

    cmd = [
        sys.executable, str(BOOK_SCRIPT),
        "--forest-id", hit["forest_id"],
        "--date", hit["date"],
        "--people", str(cfg.people),
        "--name", cfg.booker_name,
        "--phone", cfg.booker_phone,
        "--session-cache", cfg.session_cache,
        "--json",
    ]
    if not cfg.headless:
        cmd.append("--headed")
    if hit.get("room_name"):
        cmd.extend(["--room-keywords", hit["room_name"]])

    log.info("booking: %s %s %s", hit["forest"], hit["date"], hit["room_name"])
    result = subprocess.run(cmd, capture_output=True, text=True, env=dict(os.environ), timeout=180)

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"success": False, "error": result.stderr[:300] or result.stdout[:300]}


# ---------------------------------------------------------------------------
# Notification (uses foresttrip_notify module)
# ---------------------------------------------------------------------------

def _notify(message: str, cfg: WatcherConfig) -> None:
    try:
        sys.path.insert(0, str(SCRIPT_DIR))
        from foresttrip_notify import send_all
        send_all(message, {"notify": cfg.notify})
    except Exception as exc:
        log.warning("notification failed: %s", exc)


def _booking_result_message(hit: dict[str, Any], booking: dict[str, Any]) -> str:
    if booking.get("dry_run"):
        return f"[테스트] {hit['forest']} {hit['date']} {hit['room_name']} 예약 감지"
    if booking.get("success"):
        lines = [
            f"✅ 자연휴양림 예약 완료!",
            f"   휴양림: {hit['forest']}",
            f"   날짜: {hit['date']}",
            f"   객실: {booking.get('room_name') or hit['room_name']}",
            f"   예약번호: {booking.get('reservation_no', '-')}",
            f"   입금계좌: {booking.get('bank_account', '-')}",
            f"   입금금액: {booking.get('bank_amount', '-')}",
            f"   입금기한: {booking.get('bank_deadline', '-')}",
        ]
        return "\n".join(lines)
    return (
        f"❌ 예약 실패 — {hit['forest']} {hit['date']}\n"
        f"   사유: {booking.get('error', '알 수 없음')}"
    )


# ---------------------------------------------------------------------------
# Status persistence
# ---------------------------------------------------------------------------

def save_status(targets: list[Target]) -> None:
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "updated_at": datetime.now().isoformat(),
        "targets": [asdict(t) for t in targets],
    }
    STATUS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

_running = True


def _handle_stop(sig: int, frame: Any) -> None:
    global _running
    log.info("stopping (signal %d)", sig)
    _running = False


def run_watcher(cfg: WatcherConfig, dry_run: bool) -> None:
    signal.signal(signal.SIGINT, _handle_stop)
    signal.signal(signal.SIGTERM, _handle_stop)

    targets = cfg.targets
    book_attempts: dict[int, int] = {}   # target index → attempt count

    log.info(
        "watcher started — %d target(s), poll every %d±%ds",
        len(targets), cfg.poll_interval, cfg.poll_jitter,
    )

    while _running:
        pending = [t for t in targets if not t.done]
        if not pending:
            log.info("all targets completed — watcher exiting")
            break

        for idx, target in enumerate(targets):
            if target.done:
                continue
            if book_attempts.get(idx, 0) >= cfg.max_book_attempts:
                log.warning("target %s reached max booking attempts, skipping", target.forest_name)
                continue

            label = target.forest_name or target.forest_id or f"target[{idx}]"
            log.info("checking vacancies for: %s", label)

            try:
                hits = check_vacancies(target, cfg.session_cache)
            except Exception as exc:
                log.warning("vacancy check error for %s: %s", label, exc)
                hits = []

            matched = filter_hits(hits, target)

            if not matched:
                log.info("  no vacancy for %s", label)
                continue

            hit = matched[0]
            log.info("  VACANCY: %s %s %s", hit["forest"], hit["date"], hit["room_name"])

            _notify(
                f"🌲 빈자리 발견! {hit['forest']} {hit['date']} {hit['room_name']} — 예약 시도 중…",
                cfg,
            )

            booking = attempt_booking(hit, cfg, dry_run)
            book_attempts[idx] = book_attempts.get(idx, 0) + 1

            msg = _booking_result_message(hit, booking)
            log.info(msg)
            _notify(msg, cfg)

            if booking.get("success"):
                target.done = True
                target.booked_date = hit["date"]
                target.booked_room = hit.get("room_name", "")
                save_status(targets)

        if not _running:
            break

        jitter = random.randint(-cfg.poll_jitter, cfg.poll_jitter)
        sleep_sec = max(60, cfg.poll_interval + jitter)
        log.info("next poll in %ds", sleep_sec)
        time.sleep(sleep_sec)

    save_status(targets)
    log.info("watcher done — status written to %s", STATUS_FILE)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Monitor foresttrip.go.kr and auto-book vacancies.")
    p.add_argument(
        "--watchlist",
        default="~/.config/k-skill/foresttrip-watchlist.yaml",
        help="Path to watchlist YAML config.",
    )
    p.add_argument("--dry-run", action="store_true", help="Detect vacancies but do not book.")
    p.add_argument("--check-deps", action="store_true", help="Check runtime deps and exit.")
    return p.parse_args()


def main() -> int:
    args = parse_args()

    if args.check_deps:
        errors = []
        try:
            import yaml  # noqa: F401
        except ImportError:
            errors.append("pyyaml not installed: pip install pyyaml")
        try:
            from playwright.sync_api import sync_playwright  # noqa: F401
        except ImportError:
            errors.append("playwright not installed: pip install playwright && python3 -m playwright install chromium")
        if errors:
            for e in errors:
                print("MISSING:", e)
            return 1
        print("deps OK")
        return 0

    watchlist_path = Path(args.watchlist).expanduser()
    if not watchlist_path.exists():
        raise SystemExit(f"watchlist not found: {watchlist_path}")

    for var in ("KSKILL_FORESTTRIP_ID", "KSKILL_FORESTTRIP_PASSWORD"):
        if not os.getenv(var):
            # Try loading from secrets.env
            secrets_path = Path("~/.config/k-skill/secrets.env").expanduser()
            if secrets_path.exists():
                for line in secrets_path.read_text().splitlines():
                    if "=" in line and not line.startswith("#"):
                        k, v = line.split("=", 1)
                        os.environ.setdefault(k.strip(), v.strip())

    for var in ("KSKILL_FORESTTRIP_ID", "KSKILL_FORESTTRIP_PASSWORD"):
        if not os.getenv(var):
            raise SystemExit(f"env var not set: {var}")

    cfg = load_config(watchlist_path)

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(
        LOG_DIR / f"watcher_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log",
        encoding="utf-8",
    )
    file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logging.getLogger().addHandler(file_handler)

    run_watcher(cfg, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
