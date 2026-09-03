# Stock Day Trader v0.17.8

NH PLUG 실데이터를 사용하는 **개인용 데이트레이딩 연구·Paper 운용 플랫폼**입니다. 현재 목표는 새로운 전략을 계속 추가하는 것이 아니라 데이터 품질, 의사결정 메타데이터, 리스크 검증, 체결 현실성, 서버 안정성을 단계적으로 높이는 것입니다.

> **현재 안전 상태:** Control v0.8.0 LOCKED · REAL ORDER OFF · Risk Score SHADOW ONLY · 전략 자동변경 OFF

## 현재 구조

`Phone PWA → Tailscale HTTPS → Lenovo localhost FastAPI → NH PLUG`

- 서버는 `127.0.0.1:8000`에만 바인딩합니다.
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

## 데이터 및 Scanner

### KR
- 공식 NH 종목마스터 기반 Safe Universe
- 현재가/호가 기본정보 및 5분봉
- 1분봉 연구 수집: 장중 WebSocket + 장후 REST 보충
- Stocks-in-Play Point-in-Time 스냅샷
- Scanner Intelligence: RVOL5/15/30/Time, Gap, ATR14%, Relative Strength, Spread, Book Imbalance
- 선택적 OpenDART Catalyst

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

## v0.17.8 Reliability

서버가 죽으면 데이터 수집·Paper·연구가 모두 멈추기 때문에 안정성을 별도 계층으로 강화했습니다.

- `watchdog.py`: localhost `/api/health`를 주기적으로 확인하고 연속 실패 시 서버 재시작
- 업데이트 중에는 watchdog이 재시작 경쟁을 하지 않도록 update flag 사용
- `preflight.py`: 새 코드를 적용하기 전에 Python compile/core import 검사
- `remote_update.cmd`: **새 코드 사전검사 → 기존 서버 종료 → 새 서버 Health 확인** 순서
- 새 서버 Health 실패 시 이전 Git commit으로 자동 rollback 후 이전 서버 재기동

Watchdog은 매매 전략이나 주문 권한을 가지지 않습니다.

## v0.17.8 KR 1m Exit Replay

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

- GOOD/PARTIAL/BAD Data Quality Audit
- Stocks-in-Play / Scanner Challenger Shadow 비교
- Strategy / Exit / Pullback / Payoff 연구
- Public-style ORB Benchmark
- Walk-Forward 개발 구간
- 미사용 Final Lockbox
- 2x Slippage + 1-bar Late Stress
- KR 1분봉 커버리지 Gate
- KR 1m Exit Replay Gate

`Robust Validation pass`와 `deploymentReady`를 분리합니다. 기존 연구 pass가 나와도 1m Exit Replay 검증이 부족하면 deploymentReady는 false입니다. NH Simulation Fill 검증과 Micro Live는 그 이후 별도 단계입니다.

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

수동 실행:

```bat
cd server
.venv\Scripts\python.exe -m uvicorn unified_app:app --host 127.0.0.1 --port 8000
```

## 주요 상태 API

- `/api/health`
- `/api/system/runtime-health`
- `/api/system/ui-health`
- `/api/research/status`
- `/api/research/final`
- `/api/research/scanner-intelligence`
- `/api/research/decision-intelligence`
- `/api/research/1m-exit-replay`
- `/api/kr/research/1m/status`
- `/api/us/research/status`

## 실전 승격 원칙

새 기능이나 높은 과거 PF만으로 실전 승격하지 않습니다. 충분한 Point-in-Time 데이터와 OOS/Walk-Forward/Lockbox/비용·체결 스트레스, 1분봉 Exit 재현, NH 모의주문 실제 체결 검증을 통과한 뒤에만 Micro Live를 검토합니다.
