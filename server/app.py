import os
import sqlite3
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime
from fastapi import FastAPI,HTTPException,Query
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from nhplug import NhplugError
load_dotenv()
from collector import DB_PATH,KST,active_candidates,bars,collector,latest_quotes,nh_call,universe_status
from paper_engine import BREAKEVEN_BUFFER_PCT,MAX_OPEN_POSITIONS,ROUND_TRIP_COST_EST,evaluate,scan,paper_enter,mark_positions,open_positions,daily_stats,force_close_all,recent_trades,validation_stats
from us_collector import us_collector,latest_us_quotes,fetch_current,US_DATA_ENABLED
from backtest_engine import run_backtest,available_codes
VERSION='0.8.0';APP_MODE=os.getenv('APP_MODE','paper').lower();ENABLE_TRADING=False;AUTO_START_COLLECTOR=os.getenv('AUTO_START_COLLECTOR','true').lower()=='true';AUTO_BACKFILL=os.getenv('AUTO_BACKFILL','true').lower()=='true';AUTO_PAPER=os.getenv('AUTO_PAPER','true').lower()=='true';PAPER_CAPITAL=float(os.getenv('PAPER_CAPITAL','10000000'));PAPER_LOOP_SEC=max(1,float(os.getenv('PAPER_LOOP_SEC','2')));PAPER_ENTRY_START=os.getenv('PAPER_ENTRY_START','09:30');PAPER_ENTRY_CUTOFF=os.getenv('PAPER_ENTRY_CUTOFF','14:50');PAPER_EOD_EXIT=os.getenv('PAPER_EOD_EXIT','15:15');SIGNAL_MAX_AGE_SEC=max(60,int(os.getenv('SIGNAL_MAX_AGE_SEC','420')));BACKFILL_COUNT=max(30,min(int(os.getenv('BACKFILL_COUNT','120')),500));BACKFILL_MAX_CODES=max(20,min(int(os.getenv('BACKFILL_MAX_CODES','140')),200));ALLOWED_ORIGINS=[x.strip() for x in os.getenv('ALLOWED_ORIGINS','https://ztman000-bot.github.io').split(',') if x.strip()]
_BACKFILL_STATUS={'running':False,'lastRunAt':None,'lastError':None,'results':{}};_PAPER_STATUS={'running':False,'startedAt':None,'lastCycleAt':None,'lastSignalBar':{},'lastError':None,'entries':0,'closed':0,'shadowSignals':0,'eodExits':0,'staleSignals':0};_PAPER_THREAD=None;_PAPER_STOP=threading.Event()
def _credentials_ready():return bool(os.getenv('NHPLUG_APP_KEY') and os.getenv('NHPLUG_APP_SECRET'))
def _safe_error(e):return {'category':getattr(e,'category','nhplug'),'code':getattr(e,'code',''),'message':getattr(e,'message',str(e))} if isinstance(e,NhplugError) else {'category':'server','code':'','message':str(e)}
def _validate_code(code):
 if len(code)!=6 or not code.isdigit():raise HTTPException(400,'6자리 국내주식 종목코드가 필요합니다.')
 return code
def _bucket_5m(dt):return dt.replace(minute=dt.minute//5*5,second=0,microsecond=0)
def _hm(t):h,m=[int(x) for x in t.split(':',1)];return h*60+m
def _period_rows(p):
 for k in ('Output_1','output_1','Output_0','output_0'):
  v=p.get(k) if isinstance(p,dict) else None
  if isinstance(v,list):return v
 return []
def _backfill_one(code,count=BACKFILL_COUNT):
 now=datetime.now(KST);cb=_bucket_5m(now);data=nh_call('/krstock/quote/v1/period',{'market_cd':'KRX','iem_cd':code,'edate':now.strftime('%Y%m%d'),'array_cnt':str(count),'gubun':'5','xtick':'5','today_cls_code':'1','fake_tick':'1'});rows=_period_rows(data)
 if not rows:raise ValueError('NH period 응답에 5분봉 배열이 없습니다.')
 written=skipped=0
 with sqlite3.connect(DB_PATH,timeout=10) as c:
  c.execute('PRAGMA journal_mode=WAL')
  for r in rows:
   ds=str(r.get('bsop_date') or '').strip();ts=str(r.get('bsop_time') or '').strip().zfill(6)
   try:dt=datetime.strptime(ds+ts,'%Y%m%d%H%M%S').replace(tzinfo=KST);b=_bucket_5m(dt);hm=b.hour*60+b.minute;o=float(r.get('stck_oprc') or 0);h=float(r.get('stck_hgpr') or 0);l=float(r.get('stck_lwpr') or 0);cl=float(r.get('stck_prpr') or 0);v=int(float(r.get('vol') or 0))
   except:skipped+=1;continue
   if b>=cb or not 540<=hm<=930 or min(o,h,l,cl)<=0:skipped+=1;continue
   c.execute('''INSERT INTO bars_5m(code,bucket,open,high,low,close,volume,sample_count) VALUES(?,?,?,?,?,?,?,0) ON CONFLICT(code,bucket) DO UPDATE SET open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,volume=excluded.volume,sample_count=0''',(code,b.isoformat(),o,h,l,cl,v));written+=1
 return {'received':len(rows),'written':written,'skipped':skipped}
def run_backfill(codes=None,count=BACKFILL_COUNT):
 if not codes:collector.wait_for_universe(timeout=20)
 target=list(codes or collector.watchlist)[:BACKFILL_MAX_CODES];_BACKFILL_STATUS.update({'running':True,'lastRunAt':datetime.now(KST).isoformat(),'lastError':None,'results':{}});errs=[]
 try:
  for code in target:
   try:_BACKFILL_STATUS['results'][code]={'ok':True,**_backfill_one(code,count)}
   except Exception as e:msg=f'{type(e).__name__}: {e}'[:500];_BACKFILL_STATUS['results'][code]={'ok':False,'error':msg};errs.append(f'{code} {msg}')
  if errs:_BACKFILL_STATUS['lastError']=' | '.join(errs)[:1000]
 finally:_BACKFILL_STATUS['running']=False
 return dict(_BACKFILL_STATUS)
def _entry_hours(n):return n.weekday()<5 and _hm(PAPER_ENTRY_START)<=n.hour*60+n.minute<_hm(PAPER_ENTRY_CUTOFF)
def _eod_due(n):return n.weekday()<5 and _hm(PAPER_EOD_EXIT)<=n.hour*60+n.minute<920
def _signal_fresh(b,n):
 try:d=datetime.fromisoformat(str(b)).astimezone(KST)
 except:return False
 return d.date()==n.date() and 0<=(n-d).total_seconds()<=SIGNAL_MAX_AGE_SEC
def _paper_loop():
 _PAPER_STATUS.update({'running':True,'startedAt':datetime.now(KST).isoformat(),'lastError':None})
 while not _PAPER_STOP.is_set():
  try:
   now=datetime.now(KST);_PAPER_STATUS['lastCycleAt']=now.isoformat();marked=mark_positions();_PAPER_STATUS['closed']+=len(marked.get('closed',[]))
   if _eod_due(now):e=force_close_all('EOD_EXIT');_PAPER_STATUS['closed']+=len(e);_PAPER_STATUS['eodExits']+=len(e)
   elif _entry_hours(now):
    for ev in scan():
     code=ev['code'];b=(ev.get('indicators') or {}).get('bucket')
     if not b or ev['action'] not in ('BUY_CANDIDATE','SHADOW_ONLY') or _PAPER_STATUS['lastSignalBar'].get(code)==b:continue
     if not _signal_fresh(b,now):_PAPER_STATUS['lastSignalBar'][code]=b;_PAPER_STATUS['staleSignals']+=1;continue
     r=paper_enter(code,PAPER_CAPITAL,evaluation=ev);_PAPER_STATUS['lastSignalBar'][code]=b
     if r.get('ok') and r.get('shadow'):_PAPER_STATUS['shadowSignals']+=1
     elif r.get('ok'):_PAPER_STATUS['entries']+=1
   _PAPER_STATUS['lastError']=None
  except Exception as e:_PAPER_STATUS['lastError']=f'{type(e).__name__}: {e}'[:500]
  _PAPER_STOP.wait(PAPER_LOOP_SEC)
 _PAPER_STATUS['running']=False
def start_paper_loop():
 global _PAPER_THREAD
 if _PAPER_THREAD and _PAPER_THREAD.is_alive():return dict(_PAPER_STATUS)
 _PAPER_STOP.clear();_PAPER_THREAD=threading.Thread(target=_paper_loop,name='paper-trading-loop',daemon=True);_PAPER_THREAD.start();return dict(_PAPER_STATUS)
def stop_paper_loop():
 _PAPER_STOP.set()
 if _PAPER_THREAD and _PAPER_THREAD.is_alive():_PAPER_THREAD.join(timeout=5)
 return dict(_PAPER_STATUS)
@asynccontextmanager
async def lifespan(app):
 if _credentials_ready() and AUTO_START_COLLECTOR:collector.start()
 if _credentials_ready() and US_DATA_ENABLED:us_collector.start()
 if _credentials_ready() and AUTO_BACKFILL:run_backfill()
 if AUTO_PAPER:start_paper_loop()
 yield
 stop_paper_loop();collector.stop();us_collector.stop()
app=FastAPI(title='Stock Day Trader NH Bridge',version=VERSION,lifespan=lifespan);app.add_middleware(CORSMiddleware,allow_origins=ALLOWED_ORIGINS,allow_credentials=False,allow_methods=['GET','POST'],allow_headers=['*'])
def _risk():return {'maxOpenPositions':MAX_OPEN_POSITIONS,'roundTripCostEstimatePct':round(ROUND_TRIP_COST_EST*100,3),'costCoverProtectPct':round(BREAKEVEN_BUFFER_PCT*100,3)}
@app.get('/api/health')
def health():return {'ok':True,'service':'stock-day-trader-nh-bridge','version':VERSION,'mode':APP_MODE,'tradingEnabled':False,'credentialsConfigured':_credentials_ready(),'baseUrl':os.getenv('NHPLUG_BASE_URL','PRODUCTION_DEFAULT'),'liveDataReady':_credentials_ready(),'autoStartCollector':AUTO_START_COLLECTOR,'autoBackfill':AUTO_BACKFILL,'autoPaper':AUTO_PAPER,'entryStart':PAPER_ENTRY_START,'entryCutoff':PAPER_ENTRY_CUTOFF,'eodExit':PAPER_EOD_EXIT,'signalMaxAgeSec':SIGNAL_MAX_AGE_SEC,'backfill':dict(_BACKFILL_STATUS),'collector':collector.status(),'usCollector':us_collector.status(),'universe':universe_status(),'paperLoop':dict(_PAPER_STATUS),'paper':daily_stats(),'validation':validation_stats(),'risk':_risk()}
def _mobile_payload():
 ev=scan();codes=[e['code'] for e in ev];quotes=latest_quotes(codes);pos=open_positions();qm={q['code']:q for q in latest_quotes([p['code'] for p in pos])};en=[]
 for p in pos:
  q=qm.get(p['code'],{});cur=float(q.get('price') or p['entry_price']);entry=float(p['entry_price']);qty=int(p['qty']);en.append({**p,'current_price':cur,'unrealized_pnl':(cur-entry)*qty,'unrealized_pct':(cur/entry-1)*100})
 return {'ok':True,'version':VERSION,'serverTime':datetime.now(KST).isoformat(),'tradingEnabled':False,'collector':collector.status(),'usCollector':us_collector.status(),'usQuotes':latest_us_quotes(),'universe':universe_status(),'paperLoop':dict(_PAPER_STATUS),'daily':daily_stats(),'validation':validation_stats(),'positions':en,'scanner':ev,'quotes':quotes,'recentTrades':recent_trades(30),'entryStart':PAPER_ENTRY_START,'entryCutoff':PAPER_ENTRY_CUTOFF,'eodExit':PAPER_EOD_EXIT,'risk':_risk()}
@app.get('/api/mobile/status')
def mobile_status():return _mobile_payload()
@app.get('/api/validation/stats')
def validation_report():return {'ok':True,**validation_stats()}
@app.get('/api/backtest/coverage')
def backtest_coverage():return {'ok':True,'controlStrategy':'v0.8.0 LOCKED','rows':available_codes()}
@app.get('/api/backtest/run')
def backtest_run(codes:str|None=Query(default=None),start:str|None=Query(default=None),end:str|None=Query(default=None),max_codes:int=40):
 parsed=[_validate_code(x.strip()) for x in codes.split(',') if x.strip()] if codes else None
 return run_backtest(parsed,start,end,max_codes)
@app.get('/api/us/status')
def us_status():return {'ok':True,'collector':us_collector.status(),'quotes':latest_us_quotes(),'paperEnabled':False,'realOrderEnabled':False}
@app.get('/api/us/test/{ticker}')
def us_test(ticker:str):
 ticker=ticker.strip().upper()
 if not ticker.isalnum() or len(ticker)>10:raise HTTPException(400,'유효한 해외주식 티커가 필요합니다.')
 try:
  q=fetch_current(ticker);return {'ok':True,'ticker':ticker,'price':q['price'],'message':'NH 해외주식 시세 조회 성공'}
 except Exception as e:err=_safe_error(e);raise HTTPException(502,f"NHPLUG US {err['category']} 오류 {err['code']}: {err['message']}")
@app.get('/api/nh/test')
def nh_test():
 if not _credentials_ready():raise HTTPException(503,'NHPLUG_APP_KEY / NHPLUG_APP_SECRET이 서버에 설정되지 않았습니다.')
 try:return {'ok':True,'code':'005930','message':'NH PLUG 인증 및 실제 시세 조회 성공','data':nh_call('/krstock/quote/v1/currentPrice',{'iem_cd':'005930','market_cd':'KRX'})}
 except Exception as e:err=_safe_error(e);raise HTTPException(502,f"NHPLUG {err['category']} 오류 {err['code']}: {err['message']}")
@app.get('/api/nh/quote/{code}')
def current_quote(code):_validate_code(code);return {'ok':True,'code':code,'data':nh_call('/krstock/quote/v1/currentPrice',{'iem_cd':code,'market_cd':'KRX'})}
@app.post('/api/collector/start')
def collector_start(codes:str|None=Query(default=None)):parsed=[_validate_code(x.strip()) for x in codes.split(',') if x.strip()] if codes else None;return {'ok':True,'collector':collector.start(parsed)}
@app.post('/api/collector/stop')
def collector_stop():return {'ok':True,'collector':collector.stop()}
@app.get('/api/collector/status')
def collector_status():return {'ok':True,'collector':collector.status(),'universe':universe_status()}
@app.post('/api/market/backfill')
def market_backfill(codes:str|None=Query(default=None),count:int=BACKFILL_COUNT):parsed=[_validate_code(x.strip()) for x in codes.split(',') if x.strip()] if codes else None;return {'ok':True,'backfill':run_backfill(parsed,max(30,min(int(count),500)))}
@app.get('/api/market/backfill/status')
def market_backfill_status():return {'ok':True,'backfill':dict(_BACKFILL_STATUS)}
@app.get('/api/market/latest')
def market_latest():return {'ok':True,'rows':latest_quotes(collector.watchlist)}
@app.get('/api/market/active')
def market_active(limit:int=40):return {'ok':True,'rows':active_candidates(limit),'universe':universe_status()}
@app.get('/api/market/bars/{code}')
def market_bars(code,limit:int=120):_validate_code(code);return {'ok':True,'code':code,'interval':'5m','rows':bars(code,limit)}
@app.get('/api/paper/scan')
def paper_scan():return {'ok':True,'rows':scan(),'daily':daily_stats(),'universe':universe_status()}
@app.get('/api/paper/evaluate/{code}')
def paper_evaluate(code):_validate_code(code);return {'ok':True,'evaluation':evaluate(code)}
@app.post('/api/paper/enter/{code}')
def paper_entry(code,capital:float=PAPER_CAPITAL):_validate_code(code);return paper_enter(code,capital)
@app.post('/api/paper/mark')
def paper_mark():return {'ok':True,**mark_positions()}
@app.get('/api/paper/positions')
def paper_positions():return {'ok':True,'open':open_positions(),'daily':daily_stats(),'loop':dict(_PAPER_STATUS)}
@app.post('/api/paper/loop/start')
def paper_loop_start():return {'ok':True,'loop':start_paper_loop()}
@app.post('/api/paper/loop/stop')
def paper_loop_stop():return {'ok':True,'loop':stop_paper_loop()}
@app.post('/api/nh/order')
def order_locked():raise HTTPException(423,'Engine v0.8.0은 NH 실주문이 하드락되어 있습니다. Paper Trading 검증 전용입니다.')
