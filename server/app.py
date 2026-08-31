import os
import sqlite3
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from dotenv import load_dotenv
from nhplug import call, NhplugError

load_dotenv()

from collector import collector, latest_quotes, bars, DB_PATH, KST
from paper_engine import evaluate, scan, paper_enter, mark_positions, open_positions, daily_stats, force_close_all, recent_trades

VERSION='0.7.4'
APP_MODE=os.getenv('APP_MODE','paper').lower()
ENABLE_TRADING=os.getenv('ENABLE_TRADING','false').lower()=='true'
AUTO_START_COLLECTOR=os.getenv('AUTO_START_COLLECTOR','true').lower()=='true'
AUTO_BACKFILL=os.getenv('AUTO_BACKFILL','true').lower()=='true'
AUTO_PAPER=os.getenv('AUTO_PAPER','true').lower()=='true'
PAPER_CAPITAL=float(os.getenv('PAPER_CAPITAL','10000000'))
PAPER_LOOP_SEC=max(1.0,float(os.getenv('PAPER_LOOP_SEC','2.0')))
PAPER_ENTRY_CUTOFF=os.getenv('PAPER_ENTRY_CUTOFF','15:15')
PAPER_EOD_EXIT=os.getenv('PAPER_EOD_EXIT','15:25')
SIGNAL_MAX_AGE_SEC=max(60,int(os.getenv('SIGNAL_MAX_AGE_SEC','420')))
BACKFILL_COUNT=max(30,min(int(os.getenv('BACKFILL_COUNT','120')),500))
ALLOWED_ORIGINS=[x.strip() for x in os.getenv('ALLOWED_ORIGINS','https://ztman000-bot.github.io').split(',') if x.strip()]

_BACKFILL_STATUS={'running':False,'lastRunAt':None,'lastError':None,'results':{}}
_PAPER_STATUS={'running':False,'startedAt':None,'lastCycleAt':None,'lastSignalBar':{},'lastError':None,'entries':0,'closed':0,'shadowSignals':0,'eodExits':0,'staleSignals':0}
_PAPER_THREAD=None; _PAPER_STOP=threading.Event()

def _credentials_ready(): return bool(os.getenv('NHPLUG_APP_KEY') and os.getenv('NHPLUG_APP_SECRET'))
def _safe_error(e):
    if isinstance(e,NhplugError): return {'category':getattr(e,'category','nhplug'),'code':getattr(e,'code',''),'message':getattr(e,'message',str(e))}
    return {'category':'server','code':'','message':str(e)}
def _validate_code(code:str):
    if len(code)!=6 or not code.isdigit(): raise HTTPException(400,'6자리 국내주식 종목코드가 필요합니다.')
    return code
def _bucket_5m(dt): return dt.replace(minute=(dt.minute//5)*5,second=0,microsecond=0)
def _hm(text): h,m=[int(x) for x in text.split(':',1)]; return h*60+m

def _period_rows(payload):
    for key in ('Output_1','output_1','Output_0','output_0'):
        value=payload.get(key) if isinstance(payload,dict) else None
        if isinstance(value,list): return value
    return []

def _backfill_one(code:str,count:int=BACKFILL_COUNT):
    now=datetime.now(KST); current_bucket=_bucket_5m(now)
    data=call('/krstock/quote/v1/period',{'market_cd':'KRX','iem_cd':code,'edate':now.strftime('%Y%m%d'),'array_cnt':str(count),'gubun':'5','xtick':'5','today_cls_code':'1','fake_tick':'1'})
    rows=_period_rows(data)
    if not rows: raise ValueError('NH period 응답에 5분봉 배열이 없습니다.')
    written=0; skipped=0
    with sqlite3.connect(DB_PATH,timeout=10) as conn:
        conn.execute('PRAGMA journal_mode=WAL')
        for r in rows:
            ds=str(r.get('bsop_date') or '').strip(); ts=str(r.get('bsop_time') or '').strip().zfill(6)
            if len(ds)!=8 or len(ts)!=6: skipped+=1; continue
            try:
                dt=datetime.strptime(ds+ts,'%Y%m%d%H%M%S').replace(tzinfo=KST); bucket=_bucket_5m(dt)
                if bucket>=current_bucket: skipped+=1; continue
                o=float(r.get('stck_oprc') or 0); h=float(r.get('stck_hgpr') or 0); l=float(r.get('stck_lwpr') or 0); c=float(r.get('stck_prpr') or 0); v=int(float(r.get('vol') or 0))
                if min(o,h,l,c)<=0: skipped+=1; continue
            except (TypeError,ValueError): skipped+=1; continue
            conn.execute('''INSERT INTO bars_5m(code,bucket,open,high,low,close,volume,sample_count) VALUES(?,?,?,?,?,?,?,0)
              ON CONFLICT(code,bucket) DO UPDATE SET open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,volume=excluded.volume,sample_count=0''',(code,bucket.isoformat(),o,h,l,c,v)); written+=1
    return {'received':len(rows),'written':written,'skipped':skipped}

def run_backfill(codes=None,count:int=BACKFILL_COUNT):
    target=list(codes or collector.watchlist)[:10]; _BACKFILL_STATUS.update({'running':True,'lastRunAt':datetime.now(KST).isoformat(),'lastError':None,'results':{}}); errors=[]
    try:
        for code in target:
            try: _BACKFILL_STATUS['results'][code]={'ok':True,**_backfill_one(code,count)}
            except Exception as exc:
                msg=f'{type(exc).__name__}: {exc}'[:500]; _BACKFILL_STATUS['results'][code]={'ok':False,'error':msg}; errors.append(f'{code} {msg}')
            time.sleep(0.35)
        if errors: _BACKFILL_STATUS['lastError']=' | '.join(errors)[:1000]
    finally: _BACKFILL_STATUS['running']=False
    return dict(_BACKFILL_STATUS)

def _entry_hours(now):
    if now.weekday()>=5: return False
    hm=now.hour*60+now.minute; return 9*60<=hm<_hm(PAPER_ENTRY_CUTOFF)
def _eod_due(now): return now.weekday()<5 and _hm(PAPER_EOD_EXIT)<=now.hour*60+now.minute<15*60+31

def _signal_fresh(bucket,now):
    try: dt=datetime.fromisoformat(str(bucket)).astimezone(KST)
    except Exception: return False
    if dt.date()!=now.date(): return False
    age=(now-dt).total_seconds()
    return 0<=age<=SIGNAL_MAX_AGE_SEC

def _paper_loop():
    _PAPER_STATUS.update({'running':True,'startedAt':datetime.now(KST).isoformat(),'lastError':None})
    while not _PAPER_STOP.is_set():
        try:
            now=datetime.now(KST); _PAPER_STATUS['lastCycleAt']=now.isoformat(); marked=mark_positions(); _PAPER_STATUS['closed']+=len(marked.get('closed',[]))
            if _eod_due(now):
                eod=force_close_all('EOD_EXIT'); _PAPER_STATUS['closed']+=len(eod); _PAPER_STATUS['eodExits']+=len(eod)
            elif _entry_hours(now):
                for ev in scan():
                    code=ev['code']; ind=ev.get('indicators') or {}; bucket=ind.get('bucket')
                    if not bucket or ev['action'] not in ('BUY_CANDIDATE','SHADOW_ONLY'): continue
                    if _PAPER_STATUS['lastSignalBar'].get(code)==bucket: continue
                    if not _signal_fresh(bucket,now):
                        _PAPER_STATUS['lastSignalBar'][code]=bucket; _PAPER_STATUS['staleSignals']+=1; continue
                    result=paper_enter(code,PAPER_CAPITAL); _PAPER_STATUS['lastSignalBar'][code]=bucket
                    if result.get('ok') and result.get('shadow'): _PAPER_STATUS['shadowSignals']+=1
                    elif result.get('ok'): _PAPER_STATUS['entries']+=1
            _PAPER_STATUS['lastError']=None
        except Exception as exc: _PAPER_STATUS['lastError']=f'{type(exc).__name__}: {exc}'[:500]
        _PAPER_STOP.wait(PAPER_LOOP_SEC)
    _PAPER_STATUS['running']=False

def start_paper_loop():
    global _PAPER_THREAD
    if _PAPER_THREAD and _PAPER_THREAD.is_alive(): return dict(_PAPER_STATUS)
    _PAPER_STOP.clear(); _PAPER_THREAD=threading.Thread(target=_paper_loop,name='paper-trading-loop',daemon=True); _PAPER_THREAD.start(); return dict(_PAPER_STATUS)
def stop_paper_loop():
    _PAPER_STOP.set()
    if _PAPER_THREAD and _PAPER_THREAD.is_alive(): _PAPER_THREAD.join(timeout=5)
    return dict(_PAPER_STATUS)

@asynccontextmanager
async def lifespan(app:FastAPI):
    if _credentials_ready():
        if AUTO_BACKFILL: run_backfill()
        if AUTO_START_COLLECTOR: collector.start()
    if AUTO_PAPER: start_paper_loop()
    yield
    stop_paper_loop(); collector.stop()

app=FastAPI(title='Stock Day Trader NH Bridge',version=VERSION,lifespan=lifespan)
app.add_middleware(CORSMiddleware,allow_origins=ALLOWED_ORIGINS,allow_credentials=False,allow_methods=['GET','POST'],allow_headers=['*'])

@app.get('/api/health')
def health(): return {'ok':True,'service':'stock-day-trader-nh-bridge','version':VERSION,'mode':APP_MODE,'tradingEnabled':False,'credentialsConfigured':_credentials_ready(),'baseUrl':os.getenv('NHPLUG_BASE_URL','PRODUCTION_DEFAULT'),'liveDataReady':_credentials_ready(),'autoStartCollector':AUTO_START_COLLECTOR,'autoBackfill':AUTO_BACKFILL,'autoPaper':AUTO_PAPER,'entryCutoff':PAPER_ENTRY_CUTOFF,'eodExit':PAPER_EOD_EXIT,'signalMaxAgeSec':SIGNAL_MAX_AGE_SEC,'backfill':dict(_BACKFILL_STATUS),'collector':collector.status(),'paperLoop':dict(_PAPER_STATUS),'paper':daily_stats()}

def _mobile_payload():
    quotes=latest_quotes(); evaluations=scan(); pos=open_positions(); qmap={q['code']:q for q in quotes}; enriched=[]
    for p in pos:
        q=qmap.get(p['code'],{}); current=float(q.get('price') or p['entry_price']); entry=float(p['entry_price']); qty=int(p['qty'])
        enriched.append({**p,'current_price':current,'unrealized_pnl':(current-entry)*qty,'unrealized_pct':(current/entry-1)*100})
    return {'ok':True,'version':VERSION,'serverTime':datetime.now(KST).isoformat(),'tradingEnabled':False,'collector':collector.status(),'paperLoop':dict(_PAPER_STATUS),'daily':daily_stats(),'positions':enriched,'scanner':evaluations,'recentTrades':recent_trades(20),'entryCutoff':PAPER_ENTRY_CUTOFF,'eodExit':PAPER_EOD_EXIT}

@app.get('/api/mobile/status')
def mobile_status(): return _mobile_payload()

MOBILE_HTML='''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><title>Stock Trader Mobile</title><style>
:root{font-family:system-ui,-apple-system,sans-serif;color-scheme:dark}body{margin:0;background:#0b1020;color:#eef2ff}.wrap{max-width:760px;margin:auto;padding:16px}.head{display:flex;justify-content:space-between;align-items:center}.muted{color:#94a3b8;font-size:13px}.grid{display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin:14px 0}.card{background:#151c31;border:1px solid #26314f;border-radius:14px;padding:13px}.big{font-size:22px;font-weight:800;margin-top:4px}.ok{color:#4ade80}.bad{color:#fb7185}.warn{color:#fbbf24}.row{display:flex;justify-content:space-between;gap:10px;padding:10px 0;border-bottom:1px solid #26314f}.row:last-child{border:0}.tag{font-size:12px;font-weight:800;padding:4px 8px;border-radius:999px;background:#26314f}.buy{background:#14532d;color:#86efac}.setup{background:#713f12;color:#fde68a}h2{font-size:16px;margin:22px 0 8px}.pos{font-weight:800}.neg{color:#fb7185}.positive{color:#4ade80}@media(min-width:600px){.grid{grid-template-columns:repeat(4,1fr)}}</style></head><body><div class="wrap"><div class="head"><div><b>NH Stock Trader</b><div class="muted" id="time">연결 중...</div></div><span class="tag" id="ver">v-</span></div><div class="grid"><div class="card"><div class="muted">서버 / 시세</div><div class="big" id="server">...</div></div><div class="card"><div class="muted">실주문</div><div class="big bad">OFF</div></div><div class="card"><div class="muted">오늘 손익</div><div class="big" id="pnl">0원</div></div><div class="card"><div class="muted">DAILY LOCK</div><div class="big" id="lock">OFF</div></div></div><div class="card"><div class="row"><span>Paper Loop</span><b id="loop">-</b></div><div class="row"><span>연속 손실</span><b id="loss">0</b></div><div class="row"><span>신규 진입 종료</span><b id="cutoff">-</b></div><div class="row"><span>EOD 청산</span><b id="eod">-</b></div></div><h2>현재 Paper 포지션</h2><div class="card" id="positions">없음</div><h2>Scanner</h2><div class="card" id="scanner">불러오는 중...</div><h2>최근 거래</h2><div class="card" id="trades">없음</div><div class="muted" style="margin:18px 2px">5초 자동 새로고침 · Tailscale 전용 권장</div></div><script>
const won=n=>Math.round(Number(n||0)).toLocaleString('ko-KR')+'원';const pct=n=>(Number(n||0)>=0?'+':'')+Number(n||0).toFixed(2)+'%';
async function load(){try{const r=await fetch('/api/mobile/status',{cache:'no-store'});const d=await r.json();document.querySelector('#ver').textContent='v'+d.version;document.querySelector('#time').textContent=new Date(d.serverTime).toLocaleString('ko-KR');document.querySelector('#server').innerHTML=d.collector.running?'<span class="ok">ONLINE</span>':'<span class="bad">OFFLINE</span>';let p=d.daily.pnl||0;let pe=document.querySelector('#pnl');pe.textContent=won(p);pe.className='big '+(p>=0?'positive':'neg');document.querySelector('#lock').innerHTML=d.daily.locked?'<span class="bad">ON</span>':'<span class="ok">OFF</span>';document.querySelector('#loop').textContent=d.paperLoop.running?'RUNNING':'STOP';document.querySelector('#loss').textContent=d.daily.consecutiveLosses;document.querySelector('#cutoff').textContent=d.entryCutoff;document.querySelector('#eod').textContent=d.eodExit;document.querySelector('#positions').innerHTML=d.positions.length?d.positions.map(x=>`<div class="row"><div><b>${x.code}</b><div class="muted">${x.qty}주 · 진입 ${won(x.entry_price)}</div></div><div style="text-align:right"><b>${won(x.current_price)}</b><div class="${x.unrealized_pnl>=0?'positive':'neg'}">${won(x.unrealized_pnl)} (${pct(x.unrealized_pct)})</div></div></div>`).join(''):'열린 포지션 없음';let ss=[...d.scanner].sort((a,b)=>b.score-a.score);document.querySelector('#scanner').innerHTML=ss.map(x=>`<div class="row"><div><b>${x.code}</b><div class="muted">Score ${x.score}</div></div><span class="tag ${x.action==='BUY_CANDIDATE'?'buy':x.action==='SETUP'?'setup':''}">${x.action}</span></div>`).join('');document.querySelector('#trades').innerHTML=d.recentTrades.length?d.recentTrades.slice(0,10).map(x=>`<div class="row"><div><b>${x.code}</b><div class="muted">${x.status}${x.exit_reason?' · '+x.exit_reason:''}</div></div><div class="${Number(x.pnl||0)>=0?'positive':'neg'}">${x.pnl==null?'-':won(x.pnl)}</div></div>`).join(''):'거래 기록 없음'}catch(e){document.querySelector('#server').innerHTML='<span class="bad">ERROR</span>';document.querySelector('#time').textContent='서버 연결 실패'}}load();setInterval(load,5000);
</script></body></html>'''

@app.get('/mobile',response_class=HTMLResponse)
def mobile_page(): return MOBILE_HTML
@app.get('/api/nh/test')
def nh_test():
    if not _credentials_ready(): raise HTTPException(503,'NHPLUG_APP_KEY / NHPLUG_APP_SECRET이 서버에 설정되지 않았습니다.')
    try: return {'ok':True,'code':'005930','message':'NH PLUG 인증 및 실제 시세 조회 성공','data':call('/krstock/quote/v1/currentPrice',{'iem_cd':'005930','market_cd':'KRX'})}
    except Exception as e:
        err=_safe_error(e); raise HTTPException(502,f"NHPLUG {err['category']} 오류 {err['code']}: {err['message']}")
@app.get('/api/nh/quote/{code}')
def current_quote(code:str): _validate_code(code); return {'ok':True,'code':code,'data':call('/krstock/quote/v1/currentPrice',{'iem_cd':code,'market_cd':'KRX'})}
@app.post('/api/collector/start')
def collector_start(codes:str|None=Query(default=None)):
    parsed=[_validate_code(x.strip()) for x in codes.split(',') if x.strip()] if codes else None; return {'ok':True,'collector':collector.start(parsed)}
@app.post('/api/collector/stop')
def collector_stop(): return {'ok':True,'collector':collector.stop()}
@app.get('/api/collector/status')
def collector_status(): return {'ok':True,'collector':collector.status()}
@app.post('/api/market/backfill')
def market_backfill(codes:str|None=Query(default=None),count:int=BACKFILL_COUNT):
    parsed=[_validate_code(x.strip()) for x in codes.split(',') if x.strip()] if codes else None; return {'ok':True,'backfill':run_backfill(parsed,max(30,min(int(count),500)))}
@app.get('/api/market/backfill/status')
def market_backfill_status(): return {'ok':True,'backfill':dict(_BACKFILL_STATUS)}
@app.get('/api/market/latest')
def market_latest(): return {'ok':True,'rows':latest_quotes()}
@app.get('/api/market/bars/{code}')
def market_bars(code:str,limit:int=120): _validate_code(code); return {'ok':True,'code':code,'interval':'5m','rows':bars(code,limit)}
@app.get('/api/paper/scan')
def paper_scan(): return {'ok':True,'rows':scan(),'daily':daily_stats()}
@app.get('/api/paper/evaluate/{code}')
def paper_evaluate(code:str): _validate_code(code); return {'ok':True,'evaluation':evaluate(code)}
@app.post('/api/paper/enter/{code}')
def paper_entry(code:str,capital:float=PAPER_CAPITAL): _validate_code(code); return paper_enter(code,capital)
@app.post('/api/paper/mark')
def paper_mark(): return {'ok':True,**mark_positions()}
@app.get('/api/paper/positions')
def paper_positions(): return {'ok':True,'open':open_positions(),'daily':daily_stats(),'loop':dict(_PAPER_STATUS)}
@app.post('/api/paper/loop/start')
def paper_loop_start(): return {'ok':True,'loop':start_paper_loop()}
@app.post('/api/paper/loop/stop')
def paper_loop_stop(): return {'ok':True,'loop':stop_paper_loop()}
@app.post('/api/nh/order')
def order_locked(): raise HTTPException(423,'v0.7.4에서도 NH 실주문은 하드락되어 있습니다. Paper Trading 검증 전용입니다.')
