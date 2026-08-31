/**
 * NH Open API adapter placeholder.
 * MockBroker와 동일한 최소 인터페이스를 구현하면 app.js 변경 없이 교체 가능합니다.
 *
 * 필수 메서드 예시:
 * - async connect(credentials)
 * - async getQuotes(codes)
 * - async getQuote(code)
 * - async getAccount()
 * - async placeOrder({code, side, qty, price, orderType})
 * - async getOrders()
 *
 * 실제 NH 문서가 제공되면 인증/호출 제한/웹소켓/주문 규격을 이 파일에만 반영합니다.
 */
export class NHAdapter {
  constructor(){this.connected=false;}
  async connect(){throw new Error('NH Open API 문서 연결 전입니다.');}
}
