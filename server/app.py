import os
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from nhplug import call, NhplugError

load_dotenv()

APP_MODE=os.getenv('APP_MODE','paper').lower()
ENABLE_TRADING=os.getenv('ENABLE_TRADING','false').lower()=='true'
ALLOWED_ORIGINS=[x.strip() for x in os.getenv('ALLOWED_ORIGINS','https://ztman000-bot.github.io').split(',') if x.strip()]

app=FastAPI(title='Stock Day Trader NH Bridge',version='0.5.0')
app.add_middleware(CORSMiddleware,allow_origins=ALLOWED_ORIGINS,allow_credentials=False,allow_methods=['GET','POST'],allow_headers=['*'])

def _credentials_ready():
    return bool(os.getenv('NHPLUG_APP_KEY') and os.getenv('NHPLUG_APP_SECRET'))

def _safe_error(e):
    # 키/토큰 원문을 브라우저로 보내지 않는다.
    if isinstance(e, NhplugError):
        return {'category':getattr(e,'category','nhplug'),'code':getattr(e,'code',''),'message':getattr(e,'message',str(e))}
    return {'category':'server','code':'','message':str(e)}

@app.get('/api/health')
def health():
    return {'ok':True,'service':'stock-day-trader-nh-bridge','version':'0.5.0','mode':APP_MODE,'tradingEnabled':ENABLE_TRADING,'credentialsConfigured':_credentials_ready(),'baseUrl':os.getenv('NHPLUG_BASE_URL','PRODUCTION_DEFAULT'),'liveDataReady':_credentials_ready()}

@app.get('/api/nh/test')
def nh_test():
    """NH 인증+현재가를 한 번에 검증. 주문은 절대 호출하지 않는다."""
    if not _credentials_ready():
        raise HTTPException(503,'NHPLUG_APP_KEY / NHPLUG_APP_SECRET이 서버에 설정되지 않았습니다.')
    try:
        data=call('/krstock/quote/v1/currentPrice',{'iem_cd':'005930','market_cd':'KRX'})
        return {'ok':True,'code':'005930','message':'NH PLUG 인증 및 실제 시세 조회 성공','data':data}
    except Exception as e:
        err=_safe_error(e)
        raise HTTPException(502,f"NHPLUG {err['category']} 오류 {err['code']}: {err['message']}")

@app.get('/api/nh/quote/{code}')
def current_quote(code:str):
    if len(code)!=6 or not code.isdigit():
        raise HTTPException(400,'6자리 국내주식 종목코드가 필요합니다.')
    if not _credentials_ready():
        raise HTTPException(503,'NH PLUG 자격증명이 설정되지 않았습니다.')
    try:
        data=call('/krstock/quote/v1/currentPrice',{'iem_cd':code,'market_cd':'KRX'})
        return {'ok':True,'code':code,'data':data}
    except Exception as e:
        err=_safe_error(e)
        raise HTTPException(502,f"NHPLUG {err['category']} 오류 {err['code']}: {err['message']}")

@app.post('/api/nh/order')
def order_locked():
    # 실제 주문은 의도적으로 하드락. ENABLE_TRADING 값과 무관하게 이 버전에서는 주문 불가.
    raise HTTPException(423,'v0.5에서는 NH 실주문이 하드락되어 있습니다. 실제 시세 수집/Paper 검증 전용입니다.')
