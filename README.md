# Stock Day Trader v0.3

NH Open API 승인 전 Paper Trading으로 검증하는 **데이트레이딩 전용 자동매매 프로토타입**입니다.

## v0.3 핵심
- 5분봉 중심 단순 의사결정: VWAP 상단 + EMA9>EMA20 + 거래량 증가 + 직전고점 돌파
- RSI/ADX-DMI는 보조 확인 필터로 사용
- 손절 -1.0%, 목표익절 +1.5%, 트레일링 스탑
- 1회 위험예산: Day Trading Capital의 0.35%
- 2회 연속 손실 시 **DAILY LOCK**: 신규 주문 중지
- DAILY LOCK 이후 Scanner와 **Shadow Trading**은 계속 실행하여 가상 성과 축적
- 손실 거래 원인을 거래량 부족, VWAP 이탈, 돌파 실패, 과매수 추격, 추세강도 부족 등으로 분류
- Learning Engine은 실전 전략을 직접 변경하지 않고 개선 후보만 제안
- 셀트리온(068270)은 `PROTECTED HOLDING`으로 지정하여 자동매매 주문 차단
- 수익의 신규 순이익 최고치 증가분을 40% 재투자 / 50% Profit Vault / 10% Risk Reserve로 분리
- Vault와 Reserve는 자동매매 주문가능금액에서 제외
- 종목별 간이 백테스트, 차트, EMA9/20, VWAP, RSI, ADX/DMI 제공
- NH Open API 연결용 Adapter는 별도 유지

## 주의
현재 시세와 캔들은 MockBroker 시뮬레이션 데이터이며 실제 투자 판단용 데이터가 아닙니다. Shadow Learning 결과와 백테스트 역시 Mock 데이터 기반입니다. 실제 NH 시세 연결 후 충분한 Paper Trading 표본으로 다시 검증해야 합니다.

실제 주문 경로는 **Market Data → Scanner → Entry Gate → Risk Engine → Execution Adapter** 순서를 유지하며 Strategy/Learning Engine이 NH 주문 API를 직접 호출하지 않도록 설계합니다.
