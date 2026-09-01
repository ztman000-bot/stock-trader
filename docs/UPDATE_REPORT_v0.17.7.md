# v0.17.7 Decision Intelligence 업데이트 보고서

작성 기준: 현재 GitHub `main` 상태를 먼저 검토한 뒤 중복/충돌 여부를 판단하여 반영.

## 1. 검토 결론

이번 요청의 핵심 4개 항목은 모두 완전한 신규 기능으로 만들 필요가 없었다.

- **메타 데이터 엔진**: v0.17.6 `scanner_intelligence.py`가 이미 Point-in-Time RVOL/Gap/ATR/Relative Strength/Spread/Book Imbalance/Catalyst 메타데이터를 저장한다. 따라서 또 다른 feature collector를 만들지 않고, 기존 데이터 + Control 평가 + 시장상태 + 포트폴리오 상태를 묶는 **Decision Metadata Layer**만 추가했다.
- **Risk Score Engine Shadow Mode**: 기존 `paper_engine.py`의 2연속손실/일손실/최대포지션/비용포함 sizing 등 Hard Risk는 유지할 가치가 높다. 이를 대체하지 않고 별도 **Advisory Risk Score Shadow**를 추가했다.
- **AI 패배 원인 분석**: 기존 `paper_engine.failure_type`, `entry_snapshot`, MFE/MAE와 `market_lab._failure_analysis`가 이미 존재한다. 이를 폐기하거나 중복하지 않고 Evidence-based Hypothesis Layer로 확장했다.
- **시장 상태 분류**: 기존 Market Lab의 NORMAL/CAUTION/RED 규칙이 이미 존재하므로 새 분류 규칙을 추가하지 않았다. 기존 threshold를 그대로 `market_state_engine.py`에 단일화해 분류 불일치 가능성만 줄였다.

## 2. 이미 구현되어 있어 유지한 기능

- Control v0.8.0 전략/진입/청산
- Safe Universe / Protected Holding
- Daily Lock / 일 최대손실 / 최대 거래횟수 / 최대 포지션
- 비용·세금·슬리피지 포함 Paper sizing
- Stocks-in-Play Point-in-Time
- Scanner Intelligence Shadow Challengers
- `failure_type` + `entry_snapshot` + MFE/MAE
- Market Lab NORMAL/CAUTION/RED
- GOOD Data Gate
- Profitability Lab / Exit Intelligence / Pullback research
- Walk-Forward / Final Lockbox / 2x Slippage + 1-bar Late Stress
- KR 1분봉 연구 수집
- US 1분/5분 Point-in-Time 연구 수집
- 40/50/10 Capital Policy
- REAL ORDER OFF / 자동 전략 변경 OFF

## 3. 이번에 실제 반영한 항목

### A. Shared Market State Engine
파일: `server/market_state_engine.py`

- 기존 Market Lab threshold를 그대로 단일 함수로 이동
- NORMAL / CAUTION / RED / UNKNOWN
- confidence / reasons / raw inputs / ruleVersion 반환
- 브로커 호출, DB 쓰기, 매매 의사결정 없음

`market_lab.py`는 이 shared engine을 사용하도록 리팩터링했지만 기존 threshold와 전략 동작은 변경하지 않았다.

### B. Decision Metadata Engine
파일: `server/decision_intelligence.py`

새로운 feature 수집기를 만들지 않고 다음 기존 결과를 조합한다.

- Control 평가 결과/점수/사유/보류사유
- 기존 Scanner Intelligence 최신 시점 스냅샷
- RVOL5/15/30/Time, Gap, ATR14, Relative Strength, Spread, Book Imbalance, Catalyst
- Shared Market State
- Daily Lock / 연속손실 / 당일 PnL / 열린 포지션 수

5분 단위로 `decision_intel_snapshots`에 저장하며 기본 보존기간은 180일이다.

### C. Risk Score Engine — Shadow Only

높은 점수는 관찰된 위험이 더 많음을 의미한다. 주요 factor:

- 기존 Daily Lock / Loss Streak
- Market State
- Liquidity / Spread / Turnover
- ATR / Gap / RVOL extreme
- RSI/ADX / VWAP stretch
- Book Imbalance
- Portfolio capacity

중요 안전조건:

- `affectsEntry = false`
- `affectsExit = false`
- `affectsSizing = false`
- `autoPromotion = false`
- REAL ORDER 영향 없음

즉 현재 Risk Score는 **예측력 검증을 위한 Shadow label**이다.

### D. Evidence-based Loss Intelligence

기존 실패분류를 그대로 유지하면서 다음 증거를 결합한다.

- 기존 `failure_type`
- `entry_snapshot`
- MFE / MAE
- 진입 당시 가장 가까운 Decision Context
- 시장상태 / Shadow Risk Score

출력은 확정적 원인이 아니라 `cause + confidence + evidence` 형태의 가설이며, winner/loser feature 평균 비교도 함께 제공한다.

### E. API / 상태 / 문서 정비

- `/api/research/decision-intelligence`
- Runtime Health / Research Status에 Decision Intelligence 상태 추가
- UI health에 Decision Metadata / Risk Shadow / Loss Intelligence / Shared Market State 플래그 추가
- UI/PWA 버전 v0.17.7
- 오래된 v0.4 README를 현재 구조 기준으로 전면 정비

## 4. 의도적으로 반영하지 않은 항목

### Risk Score로 진입 차단
**미반영.** 아직 Score가 손실 확률/PF 개선을 예측한다는 OOS 근거가 없기 때문이다.

### Risk Score로 주문수량 자동 조절
**미반영.** Hard Risk sizing은 현재 검증된 안전장치이며 새 Shadow Score로 즉시 바꾸면 전략 성과와 리스크 효과를 분리해서 평가하기 어렵다.

### AI가 전략 파라미터 자동 변경
**미반영.** 현재 Champion/Challenger 및 Lockbox 원칙과 충돌한다.

### 새로운 시장상태 threshold
**미반영.** 기존 규칙의 성과가 아직 충분히 분리 검증되지 않았으므로 이번 목적은 규칙 추가가 아니라 기준 단일화다.

### 기존 실패원인 엔진 제거
**미반영.** 기존 label은 비교 가능한 과거 기록이므로 유지하고 상위 Evidence Layer만 추가하는 편이 좋다.

### 별도 LLM/Black-box 모델
**미반영.** 현재 단계에서는 설명 가능하고 재현 가능한 feature/rule 기반 Shadow 분석이 검증에 더 적합하다. 충분한 표본이 쌓인 뒤 ML/LLM은 Challenger로 검토할 수 있다.

### 기존 전략/진입/청산 변경
**미반영.** 이번 업데이트 목적이 Decision/Risk Intelligence 강화이며 기존 Control과 성과 비교 기준을 보존해야 한다.

## 5. 진행률 평가

기능 개발률과 실전 승격률을 분리해서 봐야 한다.

- 데이터/수집 기반: **약 90%**
- Scanner/Point-in-Time 연구 기반: **약 90%**
- 전략 연구/강건성 검증 기반: **약 90%**
- Risk/Decision Intelligence 기반: **약 85%**
- 실제 체결 현실성: **약 60~65%**
- 실전 수익성 입증: **아직 미완료**
- 연구 플랫폼 전체 완성도: **약 88%**

`1m Exit Replay`, NH 모의주문 실제 fill 비교, 충분한 Forward/OOS 표본이 남아 있으므로 기능 완성도와 실전 사용 가능성을 동일하게 보지 않는다.

## 6. 앞으로의 우선순위

1. **KR 1m Exit Replay**: 같은 5분봉 안에서 stop/peak/trail 선후관계 재현
2. **Risk Shadow Calibration**: Risk Score 구간별 실제 손실률/PF/MAE/OOS 비교
3. **Loss Hypothesis Validation**: 가설별 재현성/통계적 안정성 확인
4. **Decision Metadata Forward Accumulation**: 시점 데이터 장기 축적
5. **Scanner → Entry → Exit 기여도 분해**: 어떤 단계가 PF를 개선/훼손하는지 분리
6. **NH Simulation Fill Validation**: 예상 fill과 실제 모의체결 차이 측정
7. 위 조건 통과 후에만 Micro Live 검토

## 7. 안전 결론

v0.17.7은 **새 전략 버전이 아니라 의사결정 관찰·설명·리스크 검증 계층 업데이트**다.

Control v0.8.0, 진입/청산, Hard Risk, Protected Holding, REAL ORDER OFF 상태는 유지한다.
