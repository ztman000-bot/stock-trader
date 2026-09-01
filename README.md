# Stock Day Trader v0.17.7

NH PLUG 실데이터를 사용하는 **개인용 데이트레이딩 연구·Paper 운용 플랫폼**입니다. 현재 목표는 새로운 전략을 계속 추가하는 것이 아니라 데이터 품질, 의사결정 메타데이터, 리스크 검증, 체결 현실성을 단계적으로 높이는 것입니다.

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

## v0.17.7 Decision Intelligence

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
기존 `failure_type`, 진입 `entry_snapshot`, MFE/MAE를 폐기하지 않고 재사용합니다. 그 위에 거래량 부족, 약한 추세, 과매수, 약한 시장 breadth, 높은 spread, VWAP 추격, 수익 반납, follow-through 부족, 불리한 시장상태 등을 **원인 확정이 아닌 증거 기반 가설**로 순위화합니다.

## Market State

기존 Market Lab의 NORMAL / CAUTION / RED 기준을 `market_state_engine.py`에 단일화했습니다. v0.17.7에서는 **기존 threshold를 바꾸지 않고** 코드 중복과 분류 불일치 가능성만 줄였습니다.

## 연구/검증 파이프라인

- GOOD/PARTIAL/BAD Data Quality Audit
- Stocks-in-Play / Scanner Challenger Shadow 비교
- Strategy / Exit / Pullback / Payoff 연구
- Public-style ORB Benchmark
- Walk-Forward 개발 구간
- 미사용 Final Lockbox
- 2x Slippage + 1-bar Late Stress
- KR 1분봉 커버리지 Gate

1분봉 데이터가 충분해져도 실제 `1m Exit Replay`가 연결·검증되기 전에는 Exit 검증 완료로 간주하지 않습니다.

## 자본 정책

실현이익은 연구 정책상 다음 구조를 사용합니다.

- 40% 재투자
- 50% Profit Vault
- 10% Risk Reserve

현재 `capital_policy.py` 자체에는 주문 권한이 없습니다.

## 실행

Windows에서는 기존 launcher를 사용합니다.

```bat
server\start_stock_trader_background.cmd
```

수동 실행이 필요하면:

```bat
cd server
.venv\Scripts\python.exe -m uvicorn unified_app:app --host 127.0.0.1 --port 8000
```

업데이트는 열린 Paper 포지션과 로컬 tracked 변경을 확인한 뒤 `git pull --ff-only`로 적용하고 서버 Health를 재확인합니다.

## 주요 상태 API

- `/api/health`
- `/api/system/runtime-health`
- `/api/research/status`
- `/api/research/final`
- `/api/research/scanner-intelligence`
- `/api/research/decision-intelligence`
- `/api/kr/research/1m/status`
- `/api/us/research/status`

## 실전 승격 원칙

새 기능이나 높은 과거 PF만으로 실전 승격하지 않습니다. 충분한 Point-in-Time 데이터와 OOS/Walk-Forward/Lockbox/비용·체결 스트레스, 1분봉 Exit 재현, NH 모의주문 실제 체결 검증을 통과한 뒤에만 Micro Live를 검토합니다.
