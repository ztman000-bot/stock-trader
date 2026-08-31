import os
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from nhplug import call, NhplugError

load_dotenv()

APP_MODE=os.getenv('APP_MODE','paper').lower()
ENABLE_TRADING=os.getenv('ENABLE_TRADING','false').lower()=='true'
ALLOWED_ORIGINS=[x.strip() for x in os.getenv('ALLOWED_ORIGINS','https://ztman000-bot.github.io').split(',') if x.strip()]

app=FastAPI(title='Stock Day Trader NH Bridge',version='0.4.0')
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=['GET','POST'],
    allow_headers=['*'],
)

@app.get('/api/health')
def health():
    return {
        'ok': True,
        'service': 'stock-day-trader-nh-bridge',
        'version': '0.4.0',
        'mode': APP_MODE,
        'tradingEnabled': ENABLE_TRADING,
        'credentialsConfigured': bool(os.getenv('NHPLUG_APP_KEY') and os.getenv('NHPLUG_APP_SECRET')),
        'baseUrl': os.getenv('NHPLUG_BASE_URL','PRODUCTION_DEFAULT')
    }

@app.get('/api/nh/quote/{code}')
def current_quote(code:str):
    if len(code)!=6 or not code.isdigit():
        raise HTTPException(400,'6자리 국내주식 종목코드가 필요합니다.')
    try:
        data=call('/krstock/quote/v1/currentPrice',{'iem_cd':code,'market_cd':'KRX'})
        return {'ok':True,'code':code,'data':data}
    except NhplugError as e:
        raise HTTPException(502,f'NHPLUG 오류: {e}')
    except Exception as e:
        raise HTTPException(500,f'시세 조회 실패: {e}')

@app.post('/api/nh/order')
def order_locked():
    # v0.4는 실제 주문 경로를 의도적으로 잠근다.
    # Paper/실시세 검증 완료 후 v0.5에서 Risk Engine 서명 검증과 함께 개방한다.
    raise HTTPException(423,'v0.4에서는 NH 실주문이 잠겨 있습니다. 실제 시세 수집/Paper 검증 전용입니다.')
