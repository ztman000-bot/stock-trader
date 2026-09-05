# Stock Day Trader v0.17.10

NH PLUG 실데이터를 사용하는 **개인용 데이트레이딩 연구·Paper 운용 플랫폼**입니다. 현재 목표는 새로운 전략을 계속 추가하는 것이 아니라 데이터 품질, 의사결정 메타데이터, 리스크 검증, 체결 현실성, 서버 안정성을 단계적으로 높이는 것입니다.

> **현재 안전 상태:** Control v0.8.0 LOCKED · REAL ORDER OFF · Risk Score SHADOW ONLY · 전략 자동변경 OFF

## 현재 구조

주 운용 경로는 상황에 따라 하나만 사용합니다.

- Android 임시 서버: `Phone PWA → Tailscale → Android/Termux FastAPI → NH PLUG`
- Windows 서버: `Phone PWA → Tailscale HTTPS → Lenovo localhost FastAPI → NH PLUG`

Android는 `APP_MODE=paper` 및 `ENABLE_TRADING=false`가 로컬 `.env`에서 확인되지 않으면 시작/복구/업데이트를 거부합니다. 상태변경 HTTP 요청(POST/PUT/PATCH/DELETE)은 Android에서 localhost 또는 Tailscale 100.x 클라이언트만 허용합니다.

- NH App Key / App Secret은 로컬 `server/.env`에만 저장합니다.
- GitHub/프론트엔드/채팅에 인증정보를 넣지 않습니다.
- 기존 장기보유 보호종목 `068270`은 자동매매 대상에서 제외합니다.

## Control v0.8.0

검증 전까지 실전 규칙을 자동 변경하지 않습니다.

- 5분봉 중심 VWAP / EMA9·20 / ORB·돌파 / 거래량 / RSI / ADX-DMI
- 1회 위험예산 0.35%
- 2회 연속 손실 시 DAILY LOCK
- 일 최대손실, 최대 거래횟수, 최대 동시보유 제한
- 수수료·세금·슬리피지를 포함한 Paper sizing
- 손절 / 비용회수 보호 / Trailing / EOD 관리
- DAILY LOCK 이후에도 Scanner·Shadow·연구는 계속

## v0.17.9 Reliability Hardening

서버 프로세스가 살아 있다는 사실만으로 정상이라고 판단하지 않습니다.

- Android Watchdog: PID/프로세스 identity, heartbeat, Paper loop, Collector loop를 검사
- 장중 startup grace 이후 `/api/system/runtime-health`의 실제 quote freshness까지 확인
- 업데이트 중 Watchdog 재시작 경쟁을 update flag로 차단
- `preflight.py`: 새 코드 적용 전 Python compile/core import 검사
- Safety Invariant CI: 모든 PR에서 Python 전체 compile + `test_*.py` 실행
- Android updater도 동일한 Safety Invariant를 로컬에서 통과해야 서버 교체 가능
- 새 서버 Health 실패 시 이전 Git commit으로 rollback 후 재기동
- Android requirements 변경 시 이전 pinned requirements를 재설치하고 `pip check`
- 업데이트 직전 WAL-safe SQLite snapshot + `PRAGMA quick_check(1)` 검증
- Android 장후 일일 DB snapshot 자동화, 기본 7개 보존
- EOD Paper 종료 시 최신 quote가 없으면 가짜 가격을 만들지 않고 `eod_unresolved`로 기록하며 해당 종목 수집 우선순위를 유지

Watchdog, 백업, 연구 모듈은 주문 권한을 가지지 않습니다.

## v0.17.10 Research Data Expansion

Control을 바꾸지 않고 **그 순간 프로그램이 무엇을 보고 왜 거래/보류했는지**를 Point-in-Time으로 축적합니다.

- Scanner 의사결정: BUY_CANDIDATE / SETUP / WATCH / SHADOW_ONLY / BLOCKED 등
- 종목별 score, indicator, 사유·차단사유, market breadth, 후보 메타데이터 저장
- KOSPI/KOSDAQ 후보군의 상승비율·평균 등락률·거래대금 문맥 저장
- +5/+10/+30/+60분 및 EOD 후행수익률 라벨 자동 재계산
- 공식 NH 5분봉이 장후 덮어쓰면 후행 라벨도 다시 계산 가능
- 날짜별 verified Universe Snapshot 저장으로 향후 생존편향 축소
- `entriesToday`, `entry_sequence`를 별도 계측해 `MAX_DAILY_TRADES=8` 의미를 나중에 실제 데이터로 비교
- 현재 `MAX_DAILY_TRADES=8` Control 잠금 의미는 **기존 CLOSED 거래 기준 그대로 유지**
- 관측 모듈은 NH API 호출을 추가하지 않고 로컬 SQLite/WAL만 사용

상태 API: `/api/research/observations`

## 데이터 및 Scanner

### KR
- 공식 NH 종목마스터 기반 Safe Universe
- 현재가/호가 기본정보 및 장중 5분봉
- 과거/연구용 공식 NH 기간별시세 5분봉 provenance 별도 기록
- Profitability/Robust 연구에는 **구조적으로 GOOD이면서 공식 NH provenance가 76봉 이상 확인된 code-day만 사용**
- 1분봉 연구 수집: 장중 WebSocket + 장후 REST 보충
- 1분봉 Live Focus는 열린 Paper 우선종목 → active candidates → watchlist 순으로 최대 10종목
- Stocks-in-Play Point-in-Time 스냅샷
- Scanner Intelligence: RVOL5/15/30/Time, Gap, ATR14%, Relative Strength, Spread, Book Imbalance
- 선택적 OpenDART Catalyst

장중 Control/Paper의 실시간 5분봉 경로는 provenance 연구 게이트와 분리되어 있으며 v0.17.10에서도 매매 규칙을 변경하지 않습니다.

### US
- KR과 별도 DB/통계
- 1분봉 → 완성 5분봉 집계
- Gap, Time-of-Day RVOL, Dollar Volume, Stocks-in-Play
- SPY / QQQ / IWM 시장 프록시
- US Paper OFF · US REAL ORDER OFF

## Decision Intelligence

기존 기능을 중복 계산하지 않고 현재 엔진의 결과를 하나의 의사결정 컨텍스트로 묶습니다.

### Decision Metadata Engine
- Control 평가 결과와 진입 사유/보류 사유
- Scanner Intelligence 시점 스냅샷
- RVOL / Gap / ATR / 상대강도 / Spread / Book Imbalance / Catalyst
- 시장상태 및 Confidence
- Daily Lock / 연속손실 / 당일 PnL / 열린 포지션 수

### Risk Score Engine — Shadow Only
- 점수와 위험요인을 기록하지만 **진입·청산·수량·실주문에 영향을 주지 않습니다.**
- 결과는 향후 손실률/PF/OOS와 비교해 실제 예측력이 검증된 경우에만 다음 단계를 논의합니다.

### Loss Intelligence
기존 `failure_type`, 진입 `entry_snapshot`, MFE/MAE를 재사용해 거래량 부족, 약한 추세, 과매수, 약한 시장 breadth, 높은 spread, VWAP 추격, 수익 반납, follow-through 부족, 불리한 시장상태 등을 **원인 확정이 아닌 증거 기반 가설**로 순위화합니다.

## KR 1m Exit Replay

`bars_1m`에 쌓인 완성 1분봉으로 현재 Control의 Stop / Trailing / Cost-cover / EOD 순서를 재현합니다.

1분 OHLC만으로도 1분 내부의 정확한 경로는 알 수 없으므로 각 봉을 **O-H-L-C / O-L-H-C 두 경로로 모두 재현**합니다. 두 결과가 다르면 임의로 하나를 정답 처리하지 않고 path ambiguity로 남깁니다.

- 실제 Paper 종료사유와 replay 종료사유 비교
- 경로 간 종료사유/종료분 일치율
- replay PnL 범위
- 충분한 표본과 경로 일치율이 확보되기 전까지 validation `ready=false`
- Control/진입/청산/수량/실주문 자동변경 없음

상태 API: `/api/research/1m-exit-replay`

## Market State

기존 Market Lab의 NORMAL / CAUTION / RED 기준을 `market_state_engine.py`에 단일화했습니다. threshold를 임의 변경하지 않고 코드 중복과 분류 불일치 가능성을 줄입니다.

## 연구/검증 파이프라인

- Structural GOOD/PARTIAL/BAD Data Quality Audit
- Official NH 5m provenance research gate
- Point-in-Time Scanner Decision / Universe Snapshot 축적
- Stocks-in-Play / Scanner Challenger Shadow 비교
- Strategy / Exit / Pullback / Payoff 연구
- Public-style ORB Benchmark
- **Expanding non-overlap Walk-Forward + 1거래일 purge gap**
- 최소 3개 유효 fold, 유효 fold의 75% 이상 양수 요구
- 미사용 Final Lockbox, 최소 20거래 요구
- 2x Slippage + 1-bar Late Stress
- KR 1분봉 커버리지 Gate
- KR 1m Exit Replay Gate

`Robust Validation pass`와 `deploymentReady`를 분리합니다. 연구 pass가 나와도 1m Exit Replay 검증이 부족하면 `deploymentReady=false`입니다. NH Simulation Fill 검증과 Micro Live는 그 이후 별도 단계입니다.

## 자본 정책

- 40% 재투자
- 50% Profit Vault
- 10% Risk Reserve

현재 `capital_policy.py` 자체에는 주문 권한이 없습니다.

## 실행

Windows:

```bat
server\start_stock_trader_background.cmd
```

Windows 수동 실행:

```bat
cd server
.venv\Scripts\python.exe -m uvicorn unified_app:app --host 127.0.0.1 --port 8000
```

Android/Termux:

```sh
cd ~/stock-trader
bash server/start_android.sh
```

안정성 점검:

```sh
cd ~/stock-trader
bash server/android_stability_check.sh
```

## 주요 상태 API

- `/api/health`
- `/api/system/runtime-health`
- `/api/system/ui-health`
- `/api/system/android-watchdog` (Android)
- `/api/research/status`
- `/api/research/final`
- `/api/research/data-quality`
- `/api/research/observations`
- `/api/research/scanner-intelligence`
- `/api/research/decision-intelligence`
- `/api/research/1m-exit-replay`
- `/api/kr/research/1m/status`
- `/api/us/research/status`

## 실전 승격 원칙

새 기능이나 높은 과거 PF만으로 실전 승격하지 않습니다. 충분한 Point-in-Time 데이터와 공식 NH provenance, purged/non-overlap Walk-Forward, Final Lockbox, 비용·체결 스트레스, 1분봉 Exit 재현, NH 모의주문 실제 체결 검증을 통과한 뒤에만 Micro Live를 검토합니다.