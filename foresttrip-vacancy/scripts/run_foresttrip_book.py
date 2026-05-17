#!/usr/bin/env python3
"""
Playwright-based auto-booking for foresttrip.go.kr.

Navigates to the forest's monthly reservation page, clicks the target date,
selects a matching room, fills the reservation form, chooses 무통장입금, and
submits.  Returns a JSON object with the booking outcome.

Usage (standalone):
    python3 run_foresttrip_book.py \\
        --forest-id A0000001 \\
        --date 20260601 \\
        --people 2 \\
        --name 홍길동 \\
        --phone 010-1234-5678

Or driven by the watcher via --from-watchlist to read booker info from YAML.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_SESSION_CACHE = "~/.cache/k-skill/foresttrip-vacancy/session.json"
MONTHLY_STATUS_URL = (
    "https://www.foresttrip.go.kr/rep/or/sssn/monthRsrvtSmplStatus.do"
)
LOGIN_URL = "https://www.foresttrip.go.kr/com/login.do"


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class BookingResult:
    success: bool
    forest_id: str
    date: str
    room_name: str
    reservation_no: str = ""
    bank_account: str = ""
    bank_amount: str = ""
    bank_deadline: str = ""
    error: str = ""
    screenshot: str = ""  # path to the final screenshot


# ---------------------------------------------------------------------------
# Session bootstrap (mirrors run_foresttrip_vacancy.py)
# ---------------------------------------------------------------------------

def _load_session_cookies(cache_path: Path) -> dict[str, str] | None:
    if not cache_path.exists():
        return None
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        if time.time() > float(data.get("expires_at", 0)):
            return None
        return dict(data["cookies"])
    except Exception:
        return None


def _inject_cookies(context: Any, cookies: dict[str, str]) -> None:
    for name, value in cookies.items():
        try:
            context.add_cookies([{
                "name": name,
                "value": value,
                "domain": ".foresttrip.go.kr",
                "path": "/",
                "sameSite": "Lax",
            }])
        except Exception:
            pass


def _login_and_get_cookies(page: Any, forest_id_env: str, forest_pw_env: str) -> dict[str, str]:
    page.goto(LOGIN_URL)
    page.wait_for_load_state("networkidle")
    page.fill("#mmberId", forest_id_env)
    page.fill("#gnrlMmberPssrd", forest_pw_env)
    page.click("input.loginBtn")
    page.wait_for_load_state("networkidle")
    return {c["name"]: c["value"] for c in page.context.cookies()}


# ---------------------------------------------------------------------------
# Selector helpers
# ---------------------------------------------------------------------------

def _try_click(page: Any, selectors: list[str], timeout: int = 3000) -> bool:
    for sel in selectors:
        try:
            page.click(sel, timeout=timeout)
            return True
        except Exception:
            pass
    return False


def _try_fill(page: Any, selectors: list[str], value: str) -> bool:
    for sel in selectors:
        try:
            page.fill(sel, value)
            return True
        except Exception:
            pass
    return False


def _screenshot(page: Any, out_dir: Path, name: str) -> str:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / name
    try:
        page.screenshot(path=str(path), full_page=True)
        return str(path)
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Core booking flow
# ---------------------------------------------------------------------------

def book_room(
    *,
    forest_id: str,
    use_date: str,          # YYYYMMDD
    booker_name: str,
    booker_phone: str,
    people: int,
    room_keywords: list[str] | None = None,
    headless: bool = True,
    session_cache: str = DEFAULT_SESSION_CACHE,
) -> BookingResult:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise SystemExit("playwright is required: python3 -m pip install playwright") from exc

    ss_dir = Path("~/.cache/k-skill/foresttrip-vacancy/screenshots").expanduser()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    ss_prefix = f"{ts}_{forest_id}_{use_date}_"

    cache_path = Path(session_cache).expanduser()
    env_id = os.getenv("KSKILL_FORESTTRIP_ID", "")
    env_pw = os.getenv("KSKILL_FORESTTRIP_PASSWORD", "")

    if not env_id or not env_pw:
        return BookingResult(
            success=False,
            forest_id=forest_id,
            date=use_date,
            room_name="",
            error="KSKILL_FORESTTRIP_ID / KSKILL_FORESTTRIP_PASSWORD not set",
        )

    day_str = str(int(use_date[6:8]))   # "01" → "1"
    ym_str = use_date[:6]               # "202606"

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        )

        # Inject cached cookies or do a fresh login
        cached = _load_session_cookies(cache_path)
        if cached:
            _inject_cookies(context, cached)

        page = context.new_page()

        # 1. Go to the monthly status page for this forest + month
        page.goto(
            f"{MONTHLY_STATUS_URL}?insttId={forest_id}&srchDate={ym_str}01",
            wait_until="networkidle",
        )
        _screenshot(page, ss_dir, f"{ss_prefix}01_monthly.png")

        # Re-login if redirected to login page
        if "/com/login" in page.url or "login" in page.url.lower():
            page.fill("#mmberId", env_id)
            page.fill("#gnrlMmberPssrd", env_pw)
            page.click("input.loginBtn")
            page.wait_for_load_state("networkidle")
            page.goto(
                f"{MONTHLY_STATUS_URL}?insttId={forest_id}&srchDate={ym_str}01",
                wait_until="networkidle",
            )
            _screenshot(page, ss_dir, f"{ss_prefix}01b_after_login.png")

        # 2. Click on the target date in the calendar
        #    Typical selectors on foresttrip.go.kr:
        date_clicked = _try_click(page, [
            f'td.possible a:text-is("{day_str}")',
            f'td.possible:text-is("{day_str}")',
            f'td[class*="pos"] a:text-is("{day_str}")',
            f'td[class*="avail"] a:text-is("{day_str}")',
            f'a[href*="{use_date}"]',
        ])

        if not date_clicked:
            # Fallback: JS click on any non-disabled td containing the day number
            clicked = page.evaluate(f"""
                (() => {{
                    const tds = [...document.querySelectorAll('td')];
                    for (const td of tds) {{
                        const txt = td.textContent.trim();
                        if (txt === '{day_str}' || txt.startsWith('{day_str}\\n')) {{
                            const disabledClasses = ['disabled', 'closed', 'impossible', 'full'];
                            const cls = td.className || '';
                            if (!disabledClasses.some(c => cls.includes(c))) {{
                                td.click();
                                const a = td.querySelector('a');
                                if (a) a.click();
                                return true;
                            }}
                        }}
                    }}
                    return false;
                }})()
            """)
            if not clicked:
                last_ss = _screenshot(page, ss_dir, f"{ss_prefix}ERR_no_date.png")
                return BookingResult(
                    success=False, forest_id=forest_id, date=use_date,
                    room_name="", error=f"could not click date {use_date}",
                    screenshot=last_ss,
                )

        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(1500)
        _screenshot(page, ss_dir, f"{ss_prefix}02_room_list.png")

        # 3. Find the target room and click its 예약 button
        room_name_found = ""
        booked = False

        # Gather all visible book buttons with their surrounding room name text
        room_buttons = page.evaluate("""
            (() => {
                const btns = [...document.querySelectorAll('a, button')].filter(el => {
                    const t = el.textContent.trim();
                    return t === '예약' || t === '예약하기' || t === '신청' || t === '예약신청';
                });
                return btns.map((btn, i) => {
                    const row = btn.closest('tr, li, div.item, div.goods');
                    const nameEl = row ? row.querySelector('.goodsNm, .room-name, td:first-child, .name') : null;
                    return { index: i, name: nameEl ? nameEl.textContent.trim() : '' };
                });
            })()
        """)

        # Pick the first matching room (or first available if no keyword filter)
        target_index = None
        for room_info in room_buttons:
            name = room_info.get("name", "")
            if not room_keywords:
                target_index = room_info["index"]
                room_name_found = name
                break
            if any(kw in name for kw in room_keywords):
                target_index = room_info["index"]
                room_name_found = name
                break

        if target_index is None:
            last_ss = _screenshot(page, ss_dir, f"{ss_prefix}ERR_no_room.png")
            return BookingResult(
                success=False, forest_id=forest_id, date=use_date,
                room_name="", error="no matching room found on the page",
                screenshot=last_ss,
            )

        # Click the chosen button by index
        page.evaluate(f"""
            (() => {{
                const btns = [...document.querySelectorAll('a, button')].filter(el => {{
                    const t = el.textContent.trim();
                    return t === '예약' || t === '예약하기' || t === '신청' || t === '예약신청';
                }});
                if (btns[{target_index}]) btns[{target_index}].click();
            }})()
        """)
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(1500)
        _screenshot(page, ss_dir, f"{ss_prefix}03_booking_form.png")

        # 4. Fill in the reservation form
        #    Common field IDs on foresttrip.go.kr:
        _try_fill(page, ["#applcntNm", "#memberNm", "input[name='applcntNm']"], booker_name)

        # Phone number: sometimes split into 3 parts, sometimes single field
        phone_parts = booker_phone.replace("-", "").replace(" ", "")
        if len(phone_parts) == 11:
            p1, p2, p3 = phone_parts[:3], phone_parts[3:7], phone_parts[7:]
        else:
            p1, p2, p3 = phone_parts[:3], phone_parts[3:-4], phone_parts[-4:]

        filled_phone = (
            _try_fill(page, ["#mbtlnum1"], p1)
            and _try_fill(page, ["#mbtlnum2"], p2)
            and _try_fill(page, ["#mbtlnum3"], p3)
        )
        if not filled_phone:
            _try_fill(page, [
                "#mbtlnum", "input[name='mbtlnum']", "input[placeholder*='전화']",
            ], booker_phone)

        # People count
        people_str = str(people)
        people_filled = _try_fill(page, [
            "#prtcpntCo", "input[name='prtcpntCo']",
            "select[name='prtcpntCo']",
        ], people_str)
        if not people_filled:
            # Try select dropdown
            try:
                page.select_option("select[name='prtcpntCo']", value=people_str, timeout=2000)
            except Exception:
                pass

        _screenshot(page, ss_dir, f"{ss_prefix}04_form_filled.png")

        # 5. Select 무통장입금 payment method
        paid = _try_click(page, [
            'input[name="payMthdCd"][value="BK"]',
            'input[name="payMthdCd"][value="BANK"]',
            'input[name="payMthdCd"][value="vbank"]',
            'label:has-text("무통장")',
            'input[value*="bank" i]',
            'input[value*="무통장"]',
        ])
        if not paid:
            # Try finding any radio labeled 무통장입금
            page.evaluate("""
                (() => {
                    const labels = [...document.querySelectorAll('label')];
                    for (const lbl of labels) {
                        if (lbl.textContent.includes('무통장')) {
                            lbl.click();
                            const inp = document.getElementById(lbl.htmlFor);
                            if (inp) inp.click();
                            break;
                        }
                    }
                })()
            """)

        # 6. Agree to all terms checkboxes
        page.evaluate("""
            (() => {
                document.querySelectorAll('input[type="checkbox"]').forEach(cb => {
                    if (!cb.checked) cb.click();
                });
            })()
        """)

        _screenshot(page, ss_dir, f"{ss_prefix}05_payment_terms.png")

        # 7. Submit the reservation
        submitted = _try_click(page, [
            'button:has-text("예약신청")',
            'input[type="submit"][value*="예약"]',
            'a.btn:has-text("예약신청")',
            'button[type="submit"]',
            'input[type="submit"]',
        ])
        if not submitted:
            last_ss = _screenshot(page, ss_dir, f"{ss_prefix}ERR_no_submit.png")
            return BookingResult(
                success=False, forest_id=forest_id, date=use_date,
                room_name=room_name_found,
                error="could not find or click the submit button",
                screenshot=last_ss,
            )

        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(2000)
        _screenshot(page, ss_dir, f"{ss_prefix}06_result.png")

        # 8. Parse confirmation page
        page_text = page.inner_text("body")

        reservation_no = ""
        bank_account = ""
        bank_amount = ""
        bank_deadline = ""

        # Try to extract key fields from the confirmation page
        import re
        m = re.search(r"예약번호[:\s]*([A-Z0-9\-]+)", page_text)
        if m:
            reservation_no = m.group(1).strip()

        m = re.search(r"입금계좌[:\s]*([\d\-]+)", page_text)
        if m:
            bank_account = m.group(1).strip()

        m = re.search(r"입금금액[:\s]*([\d,]+원?)", page_text)
        if m:
            bank_amount = m.group(1).strip()

        m = re.search(r"입금기한[:\s]*([^\n]+)", page_text)
        if m:
            bank_deadline = m.group(1).strip()

        # If no reservation number found, check for error messages
        error_keywords = ["실패", "오류", "이미 예약", "마감", "불가"]
        if not reservation_no and any(k in page_text for k in error_keywords):
            last_ss = str(ss_dir / f"{ss_prefix}06_result.png")
            return BookingResult(
                success=False, forest_id=forest_id, date=use_date,
                room_name=room_name_found,
                error="reservation failed — see screenshot",
                screenshot=last_ss,
            )

        success = bool(reservation_no) or "예약완료" in page_text or "신청완료" in page_text
        last_ss = str(ss_dir / f"{ss_prefix}06_result.png")

        browser.close()

    return BookingResult(
        success=success,
        forest_id=forest_id,
        date=use_date,
        room_name=room_name_found,
        reservation_no=reservation_no,
        bank_account=bank_account,
        bank_amount=bank_amount,
        bank_deadline=bank_deadline,
        error="" if success else "reservation outcome unclear — see screenshot",
        screenshot=last_ss,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Auto-book a foresttrip.go.kr reservation.")
    p.add_argument("--forest-id", required=True, help="Forest insttId.")
    p.add_argument("--date", required=True, help="Target date YYYYMMDD.")
    p.add_argument("--people", type=int, required=True, help="Number of people.")
    p.add_argument("--name", required=True, help="Booker name (한국어 가능).")
    p.add_argument("--phone", required=True, help="Phone number (010-xxxx-xxxx).")
    p.add_argument(
        "--room-keywords",
        help="Comma-separated substrings to match room name (empty = any room).",
    )
    p.add_argument(
        "--session-cache",
        default=DEFAULT_SESSION_CACHE,
        help="Session cache path.",
    )
    p.add_argument("--headed", action="store_true", help="Run browser in headed mode.")
    p.add_argument("--json", dest="json_out", action="store_true", help="JSON output.")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    keywords = (
        [k.strip() for k in args.room_keywords.split(",") if k.strip()]
        if args.room_keywords
        else None
    )
    result = book_room(
        forest_id=args.forest_id,
        use_date=args.date,
        booker_name=args.name,
        booker_phone=args.phone,
        people=args.people,
        room_keywords=keywords,
        headless=not args.headed,
        session_cache=args.session_cache,
    )
    if args.json_out:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        if result.success:
            print(f"✅ 예약 성공!")
            print(f"   예약번호  : {result.reservation_no}")
            print(f"   입금계좌  : {result.bank_account}")
            print(f"   입금금액  : {result.bank_amount}")
            print(f"   입금기한  : {result.bank_deadline}")
        else:
            print(f"❌ 예약 실패: {result.error}")
        if result.screenshot:
            print(f"   스크린샷  : {result.screenshot}")
    return 0 if result.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
