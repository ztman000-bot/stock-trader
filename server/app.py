import os
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from nhplug import call, NhplugError

from collector import collector, latest_quotes, bars

load_dotenv()

APP_MODE=os.getenv('APP_MODE','paper').lower()
ENABLE_TRADING=os.getenv('ENABLE_TRADING','false').lower()=='true'
ALLOWED_ORIGINS=[x.strip() for x in os.getenv('ALLOWED_ORIGINS','https://ztman000-bot.github.io').split(',') if x.strip()]

app=FastAPI(title='Stock Day Trader NH Bridge',version='0.6.0')
app.add_middleware(CORSMiddleware,allow_origins=ALLOWED_ORIGINS,allow_credentials=False,allow_methods=['GET','POST'],allow_headers=['*'])

def _credentials_ready():
    return bool(os.getenv('NHPLUG_APP_KEY') and os.getenv('NHPLUG_APP_SECRET'))

def _safe_error(e):
    if isinstance(e, NhplugError):
        return {'category':getattr(e,'category','nhplug'),'code':getattr(e,'code',''),'message':getattr(e,'message',str(e))}
    return {'category':'server','code':'','message':str(e)}

def _validate_code(code:str):
    if len(code)!=6 or not code.isdigit():
        raise HTTPException(400,'6자리 국내주식 종목코드가 필요합니다.')
    return code

@app.get('/api/health')
def health():
    return {
        'ok':True,
        'service':'stock-day-trader-nh-bridge',
        'version':'0.6.0',
        'mode':APP_MODE,
        'tradingEnabled':ENABLE_TRADING,
        'credentialsConfigured':_credentials_ready(),
        'baseUrl':os.getenv('NHPLUG_BASE_URL','PRODUCTION_DEFAULT'),
        'liveDataReady':_credentials_ready(),
        'collector':collector.status(),
    }

@app.get('/api/nh/test')
def nh_test():
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
    _validate_code(code)
    if not _credentials_ready():
        raise HTTPException(503,'NH PLUG 자격증명이 설정되지 않았습니다.')
    try:
        data=call('/krstock/quote/v1/currentPrice',{'iem_cd':code,'market_cd':'KRX'})
        return {'ok':True,'code':code,'data':data}
    except Exception as e:
        err=_safe_error(e)
        raise HTTPException(502,f"NHPLUG {err['category']} 오류 {err['code']}: {err['message']}")

@app.post('/api/collector/start')
def collector_start(codes:str|None=Query(default=None,description='쉼표로 구분한 최대 10개 종목코드')):
    if not _credentials_ready():
        raise HTTPException(503,'NH PLUG 자격증명이 설정되지 않았습니다.')
    parsed=None
    if codes:
        parsed=[_validate_code(x.strip()) for x in codes.split(',') if x.strip()]
    status=collector.start(parsed)
    return {'ok':True,'message':'NH 실제 시세 수집 시작','collector':status}

@app.post('/api/collector/stop')
def collector_stop():
    return {'ok':True,'message':'시세 수집 중지','collector':collector.stop()}

@app.get('/api/collector/status')
def collector_status():
    return {'ok':True,'collector':collector.status()}

@app.get('/api/market/latest')
def market_latest():
    return {'ok':True,'rows':latest_quotes()}

@app.get('/api/market/bars/{code}')
def market_bars(code:str,limit:int=120):
    _validate_code(code)
    return {'ok':True,'code':code,'interval':'5m','rows':bars(code,limit)}

@app.post('/api/nh/order')
def order_locked():
    raise HTTPException(423,'v0.6에서는 NH 실주문이 하드락되어 있습니다. 실제 시세 수집/Paper 검증 전용입니다.')
