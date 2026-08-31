import os
import sqlite3
import time
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from nhplug import call, NhplugError

load_dotenv()

from collector import collector, latest_quotes, bars, DB_PATH, KST
from paper_engine import evaluate, scan, paper_enter, mark_positions, open_positions, daily_stats

APP_MODE=os.getenv('APP_MODE','paper').lower()
ENABLE_TRADING=os.getenv('ENABLE_TRADING','false').lower()=='true'
AUTO_START_COLLECTOR=os.getenv('AUTO_START_COLLECTOR','true').lower()=='true'
AUTO_BACKFILL=os.getenv('AUTO_BACKFILL','true').lower()=='true'
BACKFILL_COUNT=max(30,min(int(os.getenv('BACKFILL_COUNT','120')),500))
ALLOWED_ORIGINS=[x.strip() for x in os.getenv('ALLOWED_ORIGINS','https://ztman000-bot.github.io').split(',') if x.strip()]

_BACKFILL_STATUS={
    'running':False,
    'lastRunAt':None,
    'lastError':None,
    'results':{},
}


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


def _bucket_5m(dt):
    return dt.replace(minute=(dt.minute//5)*5,second=0,microsecond=0)


def _period_rows(payload):
    for key in ('Output_1','output_1','Output_0','output_0'):
        value=payload.get(key) if isinstance(payload,dict) else None
        if isinstance(value,list):
            return value
    return []


def _backfill_one(code:str,count:int=BACKFILL_COUNT):
    now=datetime.now(KST)
    current_bucket=_bucket_5m(now)
    data=call('/krstock/quote/v1/period',{
        'market_cd':'KRX',
        'iem_cd':code,
        'edate':now.strftime('%Y%m%d'),
        'array_cnt':str(count),
        'gubun':'5',
        'xtick':'5',
        'today_cls_code':'1',
        'fake_tick':'1',
    })
    rows=_period_rows(data)
    if not rows:
        raise ValueError('NH period 응답에 5분봉 배열이 없습니다.')

    written=0
    skipped=0
    with sqlite3.connect(DB_PATH,timeout=10) as conn:
        conn.execute('PRAGMA journal_mode=WAL')
        for r in rows:
            ds=str(r.get('bsop_date') or '').strip()
            ts=str(r.get('bsop_time') or '').strip().zfill(6)
            if len(ds)!=8 or len(ts)!=6:
                skipped+=1
                continue
            try:
                dt=datetime.strptime(ds+ts,'%Y%m%d%H%M%S').replace(tzinfo=KST)
                bucket=_bucket_5m(dt)
                if bucket>=current_bucket:
                    skipped+=1
                    continue
                o=float(r.get('stck_oprc') or 0)
                h=float(r.get('stck_hgpr') or 0)
                l=float(r.get('stck_lwpr') or 0)
                c=float(r.get('stck_prpr') or 0)
                v=int(float(r.get('vol') or 0))
                if min(o,h,l,c)<=0:
                    skipped+=1
                    continue
            except (TypeError,ValueError):
                skipped+=1
                continue
            conn.execute('''
                INSERT INTO bars_5m(code,bucket,open,high,low,close,volume,sample_count)
                VALUES(?,?,?,?,?,?,?,0)
                ON CONFLICT(code,bucket) DO UPDATE SET
                  open=excluded.open,
                  high=excluded.high,
                  low=excluded.low,
                  close=excluded.close,
                  volume=excluded.volume,
                  sample_count=0
            ''',(code,bucket.isoformat(),o,h,l,c,v))
            written+=1
    return {'received':len(rows),'written':written,'skipped':skipped}


def run_backfill(codes=None,count:int=BACKFILL_COUNT):
    target=list(codes or collector.watchlist)[:10]
    _BACKFILL_STATUS['running']=True
    _BACKFILL_STATUS['lastRunAt']=datetime.now(KST).isoformat()
    _BACKFILL_STATUS['lastError']=None
    _BACKFILL_STATUS['results']={}
    errors=[]
    try:
        for code in target:
            try:
                _BACKFILL_STATUS['results'][code]={'ok':True,**_backfill_one(code,count)}
            except Exception as exc:
                msg=f'{type(exc).__name__}: {exc}'[:500]
                _BACKFILL_STATUS['results'][code]={'ok':False,'error':msg}
                errors.append(f'{code} {msg}')
            time.sleep(0.35)
        if errors:
            _BACKFILL_STATUS['lastError']=' | '.join(errors)[:1000]
    finally:
        _BACKFILL_STATUS['running']=False
    return dict(_BACKFILL_STATUS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if _credentials_ready():
        if AUTO_BACKFILL:
            run_backfill()
        if AUTO_START_COLLECTOR:
            collector.start()
    yield
    collector.stop()


app=FastAPI(title='Stock Day Trader NH Bridge',version='0.7.1',lifespan=lifespan)
app.add_middleware(CORSMiddleware,allow_origins=ALLOWED_ORIGINS,allow_credentials=False,allow_methods=['GET','POST'],allow_headers=['*'])


@app.get('/api/health')
def health():
    return {
        'ok':True,
        'service':'stock-day-trader-nh-bridge',
        'version':'0.7.1',
        'mode':APP_MODE,
        'tradingEnabled':ENABLE_TRADING,
        'credentialsConfigured':_credentials_ready(),
        'baseUrl':os.getenv('NHPLUG_BASE_URL','PRODUCTION_DEFAULT'),
        'liveDataReady':_credentials_ready(),
        'autoStartCollector':AUTO_START_COLLECTOR,
        'autoBackfill':AUTO_BACKFILL,
        'backfill':dict(_BACKFILL_STATUS),
        'collector':collector.status(),
        'paper':daily_stats(),
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
        return {'ok':True,'code':code,'data':call('/krstock/quote/v1/currentPrice',{'iem_cd':code,'market_cd':'KRX'})}
    except Exception as e:
        err=_safe_error(e)
        raise HTTPException(502,f"NHPLUG {err['category']} 오류 {err['code']}: {err['message']}")


@app.post('/api/collector/start')
def collector_start(codes:str|None=Query(default=None)):
    if not _credentials_ready():
        raise HTTPException(503,'NH PLUG 자격증명이 설정되지 않았습니다.')
    parsed=[_validate_code(x.strip()) for x in codes.split(',') if x.strip()] if codes else None
    return {'ok':True,'message':'NH 실제 시세 수집 시작','collector':collector.start(parsed)}


@app.post('/api/collector/stop')
def collector_stop():
    return {'ok':True,'message':'시세 수집 중지','collector':collector.stop()}


@app.get('/api/collector/status')
def collector_status():
    return {'ok':True,'collector':collector.status()}


@app.post('/api/market/backfill')
def market_backfill(codes:str|None=Query(default=None),count:int=BACKFILL_COUNT):
    if not _credentials_ready():
        raise HTTPException(503,'NH PLUG 자격증명이 설정되지 않았습니다.')
    parsed=[_validate_code(x.strip()) for x in codes.split(',') if x.strip()] if codes else None
    count=max(30,min(int(count),500))
    return {'ok':True,'backfill':run_backfill(parsed,count)}


@app.get('/api/market/backfill/status')
def market_backfill_status():
    return {'ok':True,'backfill':dict(_BACKFILL_STATUS)}


@app.get('/api/market/latest')
def market_latest():
    return {'ok':True,'rows':latest_quotes()}


@app.get('/api/market/bars/{code}')
def market_bars(code:str,limit:int=120):
    _validate_code(code)
    return {'ok':True,'code':code,'interval':'5m','rows':bars(code,limit)}


@app.get('/api/paper/scan')
def paper_scan():
    return {'ok':True,'rows':scan(),'daily':daily_stats()}


@app.get('/api/paper/evaluate/{code}')
def paper_evaluate(code:str):
    _validate_code(code)
    return {'ok':True,'evaluation':evaluate(code)}


@app.post('/api/paper/enter/{code}')
def paper_entry(code:str,capital:float=10_000_000):
    _validate_code(code)
    return paper_enter(code,capital)


@app.post('/api/paper/mark')
def paper_mark():
    return {'ok':True,**mark_positions()}


@app.get('/api/paper/positions')
def paper_positions():
    return {'ok':True,'open':open_positions(),'daily':daily_stats()}


@app.post('/api/nh/order')
def order_locked():
    raise HTTPException(423,'v0.7.1에서도 NH 실주문은 하드락되어 있습니다. 실제 시세 기반 Paper Trading 검증 전용입니다.')
