---
name: foresttrip-vacancy
description: Monitor and auto-book Korean national forest recreation (자연휴양림) lodging or camping on foresttrip.go.kr. Use when the user asks for 숲나들e 빈 객실 조회, 자동예약, 빈자리 나오면 예약, watcher 실행.
license: MIT
metadata:
  category: travel
  locale: ko-KR
  phase: v2.0
---

# Foresttrip Vacancy & Auto-Booking

## What this skill does

숲나들e(`https://foresttrip.go.kr`)에서 자연휴양림 빈 객실을 주기적으로 감시하다가, 원하는 날짜에 빈자리가 생기면 **자동으로 무통장입금 예약을 완료**하고 카카오톡/Slack으로 알림을 전송한다.

| 스크립트 | 기능 |
|----------|------|
| `run_foresttrip_vacancy.py` | 단발 조회 (read-only) |
| `run_foresttrip_watcher.py` | 감시 루프 + 자동예약 트리거 |
| `run_foresttrip_book.py` | Playwright 예약 자동화 (무통장입금) |
| `foresttrip_notify.py` | 카카오톡 / Slack / macOS 알림 |

## When to use

- "이번 주말 자연휴양림 빈 객실 있어?" → 단발 조회
- "숲나들e 5월 4일 예약 가능한 곳 조회해줘"
- "유명산 빈자리 생기면 바로 예약해줘" → watcher 실행
- "자연휴양림 감시 시작해줘"

## When not to use

- 캡차를 풀거나 대기열을 우회해야 하는 경우
- 계정 정보를 채팅창에 직접 넣으려는 경우
- aggressive 스나이핑·반복 예약 시도가 필요한 경우

## Prerequisites

- Python 3.9+
- Playwright Chromium browser
- PyYAML (watcher only)

```bash
python3 -m pip install playwright pyyaml
python3 -m playwright install chromium
python3 scripts/run_foresttrip_vacancy.py --check-deps
python3 scripts/run_foresttrip_watcher.py --check-deps
```

## Required environment variables

- `KSKILL_FORESTTRIP_ID`
- `KSKILL_FORESTTRIP_PASSWORD`

Optional:

- none

### Credential resolution order

1. **이미 환경변수에 있으면** 그대로 사용한다.
2. **에이전트가 자체 secret vault(1Password CLI, Bitwarden CLI, macOS Keychain 등)를 사용 중이면** 거기서 꺼내 환경변수로 주입해도 된다.
3. **`~/.config/k-skill/secrets.env`** (기본 fallback) — plain dotenv 파일, 퍼미션 `0600`.
4. **아무것도 없으면** 유저에게 물어서 2 또는 3에 저장한다.

기본 경로에 저장하는 것은 fallback일 뿐, 강제가 아니다.
Helper 자체는 `KSKILL_FORESTTRIP_ID`, `KSKILL_FORESTTRIP_PASSWORD` 환경변수만 읽는다. vault나 `secrets.env` 를 사용하는 경우에도 실행 전에 해당 값을 환경변수로 주입한다.

## Inputs

- 날짜: `YYYYMMDD`, 여러 날짜면 comma-separated `YYYYMMDD,YYYYMMDD`
- 조회 범위:
  - `--all`: 전체 자연휴양림 조회
  - `--forest-id`: 특정 `insttId` 조회
  - `--forest-name`: 공식 휴양림명 부분 일치 조회
- 출력 형식:
  - `--text`: 사람용 요약
  - `--json`: 구조화 결과
- 선택 필터:
  - `--categories 01`: 숙박
  - `--categories 02`: 야영/캠핑
  - `--categories 01,02`: 숙박 + 야영/캠핑
- 고급 실행 옵션:
  - `--week-range N`: `--dates` 를 생략했을 때만 오늘부터 N주 범위를 조회
  - `--concurrency N`: 병렬 조회 worker 수, 1-5 범위
  - `--session-cache PATH`: 로그인 세션 캐시 경로 override

## Workflow

### 1. Credentials 확인

`KSKILL_FORESTTRIP_ID`, `KSKILL_FORESTTRIP_PASSWORD` 가 설정되어 있는지 확인한다.
없으면 `~/.config/k-skill/secrets.env` (plain dotenv, permission 0600) 에서 로드한다.

시크릿이 없다는 이유로 대체 사이트, 캡차 우회, 비공식 경로를 찾지 않는다.

### 2. 의존성 설치

```bash
python3 -m pip install playwright pyyaml
python3 -m playwright install chromium
```

### 3a. 단발 조회 (read-only)

```bash
# 전체 휴양림, 특정 날짜
python3 scripts/run_foresttrip_vacancy.py --all --text --dates 20260607

# 특정 휴양림명, JSON 출력
python3 scripts/run_foresttrip_vacancy.py --forest-name 유명산 --json --dates 20260607,20260608

# 야영/캠핑만
python3 scripts/run_foresttrip_vacancy.py --all --text --dates 20260607 --categories 02
```

### 3b. 감시 + 자동예약 (watcher)

1. watchlist 설정 파일 준비:
```bash
cp foresttrip-vacancy/config/watchlist-example.yaml ~/.config/k-skill/foresttrip-watchlist.yaml
chmod 600 ~/.config/k-skill/foresttrip-watchlist.yaml
# 편집: 대상 휴양림, 날짜, 예약자 정보, Slack webhook 등 입력
```

2. 감시 시작:
```bash
# 실제 예약
python3 scripts/run_foresttrip_watcher.py --watchlist ~/.config/k-skill/foresttrip-watchlist.yaml

# 테스트 (예약 없이 감지만)
python3 scripts/run_foresttrip_watcher.py --watchlist ~/.config/k-skill/foresttrip-watchlist.yaml --dry-run
```

3. 백그라운드 실행 (로그 저장):
```bash
nohup python3 scripts/run_foresttrip_watcher.py \
    --watchlist ~/.config/k-skill/foresttrip-watchlist.yaml \
    >> ~/.cache/k-skill/foresttrip-vacancy/logs/watcher.log 2>&1 &
echo "PID: $!"
```

### 3c. 단건 수동 예약

```bash
python3 scripts/run_foresttrip_book.py \
    --forest-id A0000001 \
    --date 20260607 \
    --people 2 \
    --name 홍길동 \
    --phone 010-1234-5678
```

### 4. 결과 확인

조회 결과는 아래 항목 중심으로 정리한다:
- 조회 날짜 / 조회 범위
- 예약 가능 휴양림명 / 객실명 / 숙박·야영 구분 / 수용 인원
- fetch failure 개수 (있으면)

자동예약 성공 시 알림 내용:
- 예약번호 / 입금계좌 / 입금금액 / 입금기한

결과가 없으면 "조회 시점 기준 예약 가능 객실 없음" 으로 안내한다.

## Done when

**단발 조회:**
- 요청 날짜와 범위가 명확하다.
- vacancy helper를 최소 1회 실행했다.
- 빈 객실 유무를 명확히 답했다.

**감시 + 자동예약:**
- watchlist.yaml 에 대상/예약자 정보가 입력됐다.
- watcher가 정상 시작됐다 (첫 폴링 사이클 로그 확인).
- 예약 성공 시 알림이 전송됐고, 예약번호가 확인됐다.

## Failure modes

| 증상 | 조치 |
|------|------|
| 로그인 실패 | `KSKILL_FORESTTRIP_ID` / `KSKILL_FORESTTRIP_PASSWORD` 확인 |
| Playwright 미설치 | `python3 -m playwright install chromium` |
| PyYAML 없음 | `pip install pyyaml` |
| 세션 만료 | `--refresh-session` 또는 session cache 삭제 후 재실행 |
| 예약 버튼 못 찾음 | `--headed` 플래그로 브라우저 직접 확인, 스크린샷 참고 |
| 숲나들e UI 변경 | `run_foresttrip_book.py` 의 셀렉터 업데이트 필요 |
| fetch failure | 결과와 실패 개수 함께 보고, `--refresh-session` 으로 1회 재조회 |

## Maintainer review notes

계정 없이 가능한 검증:

```bash
python3 -m py_compile foresttrip-vacancy/scripts/run_foresttrip_vacancy.py
python3 -m py_compile foresttrip-vacancy/scripts/run_foresttrip_watcher.py
python3 -m py_compile foresttrip-vacancy/scripts/run_foresttrip_book.py
python3 -m py_compile foresttrip-vacancy/scripts/foresttrip_notify.py
python3 foresttrip-vacancy/scripts/run_foresttrip_vacancy.py --check-deps
python3 foresttrip-vacancy/scripts/run_foresttrip_watcher.py --check-deps
```

실제 live smoke는 숲나들e 계정을 가진 사용자가 수행한다.
PR에는 비민감 요약만 남기고 계정·세션·개인정보는 포함하지 않는다.

## Safety notes

- 폴링 간격 기본 180초 ± 30초 (jitter) — 서버에 무리를 주지 않는다.
- 무통장입금 방식만 지원한다 (신용카드 정보 저장 불필요).
- 캡차 처리, 대기열 우회, 공격적 스나이핑은 하지 않는다.
- 계정 시크릿은 환경변수 또는 `~/.config/k-skill/secrets.env` 로만 다룬다.
- 스크린샷은 `~/.cache/k-skill/foresttrip-vacancy/screenshots/` 에만 저장된다.
