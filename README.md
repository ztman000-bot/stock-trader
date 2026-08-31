# Stock Day Trader v0.4

NH PLUG 승인 이후 실제 시세 연결을 준비한 **데이트레이딩 전용 자동매매 프로토타입**입니다. 프론트엔드는 GitHub Pages, NH 인증정보는 별도 Python 백엔드의 `.env`에만 저장하도록 분리했습니다.

## 데이트레이딩 개선안
- 5분봉 중심 단순 의사결정: VWAP 상단 + EMA9>EMA20 + 거래량 증가 + 직전고점 돌파
- RSI/ADX-DMI는 보조 확인 필터
- 손절 -1.0%, 목표익절 +1.5%, 트레일링 스탑
- 1회 위험예산: Day Trading Capital의 0.35%
- 2회 연속 손실 시 DAILY LOCK: 신규 주문 중지
- DAILY LOCK 이후 Scanner와 Shadow Trading은 계속 실행
- 손실 원인을 거래량 부족, VWAP 이탈, 돌파 실패, 과매수 추격, 추세강도 부족 등으로 분류
- Learning Engine은 실전 파라미터를 직접 바꾸지 않고 개선 후보만 제안
- 셀트리온(068270)은 PROTECTED HOLDING으로 자동매매 주문 차단
- 누적 순이익 최고치 증가분을 40% 재투자 / 50% Profit Vault / 10% Risk Reserve로 분리

## NH PLUG 연결 구조
`GitHub Pages UI → HTTPS → Python FastAPI Bridge → nhplug SDK → NH PLUG API`

공식 Python SDK를 사용합니다.

```bash
cd server
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app:app --host 0.0.0.0 --port 8000
```

`.env`에는 본인이 발급받은 `NHPLUG_APP_KEY`, `NHPLUG_APP_SECRET`을 직접 입력합니다. **실제 키/시크릿을 GitHub, 프론트엔드 JS, 채팅에 올리지 마세요.** `.gitignore`에서 `.env`를 차단합니다.

개발 기본값은 `NHPLUG_BASE_URL=https://moapi.nhplug.com:8443`, `APP_MODE=paper`, `ENABLE_TRADING=false`입니다.

## v0.4에서 열린 NH 기능
- `/api/health`: 백엔드/환경/키 설정 여부 확인
- `/api/nh/quote/{code}`: 공식 `nhplug.call()`을 이용한 국내주식 현재가 조회
- 프론트에서 HTTPS 백엔드 주소 저장 및 연결 상태 확인

## 실주문 잠금
v0.4의 `/api/nh/order`는 HTTP 423으로 항상 차단됩니다. 실제 NH 시세로 Paper Trading 표본을 충분히 쌓고 Risk/체결 동기화를 검증한 뒤 다음 버전에서만 주문 경로를 개방합니다.

현재 GitHub Pages의 전략 실행은 아직 MockBroker fallback입니다. NH 백엔드가 배포되면 먼저 실제 시세 조회를 검증하고, 다음 단계에서 5분봉/실시간 체결을 Market Data Adapter로 교체합니다.
