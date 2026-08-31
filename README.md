# Stock Trader v0.2

NH Open API 승인 전에도 동작하는 **Paper Trading용 주식 자동매매 프로토타입**입니다.

## 포함 기능
- MockBroker 기반 실시간형 가격 변동
- 좋은 매매위치 TOP10 스캐너
- EMA5/20/60, RSI(14), ADX/DMI 기반 전략 점수
- 캔들 차트 + EMA5/20 표시
- 종목별 간이 백테스트(거래수/승률/누적수익/MDD)
- Paper 매수/매도 및 localStorage 저장
- 자동매매 ON/OFF
- 익절/손절/점수하락 자동 청산
- 1회 주문/종목비중/보유종목수/일손실/연속손실 Risk Engine
- 긴급 Kill Switch
- NH Open API 연결용 `js/nhAdapter.js` 분리
- PWA 기본 구조

## 현재 데이터 주의
v0.2의 시세/캔들/백테스트는 모두 MockBroker 시뮬레이션 데이터입니다. 실제 투자 판단용 실시간 데이터가 아닙니다. NH API 승인 후 Market Data Adapter를 교체합니다.

## NH Open API 연동 시 교체 지점
현재 `app.js`는 `MockBroker`를 사용합니다. NH 문서가 확보되면 `nhAdapter.js`에 인증, 현재가/차트, 계좌잔고, 주문/정정/취소, 호출 제한과 재시도를 구현합니다.

실제 주문은 Strategy가 직접 호출하지 않고 **Strategy → RiskEngine → Execution Adapter** 순서를 유지합니다.

## 기본 리스크 값
- 초기 Paper 현금: 10,000,000원
- 1회 최대 주문: 100,000원
- 종목 최대 비중: 10%
- 최대 보유: 5종목
- 일일 최대 손실: -1%
- 연속 손실 3회: 신규 진입 차단
- 익절 +3.5%, 손절 -2.0%

`js/config.js`에서 수정할 수 있습니다.
