"""Multi-day NH 5m accumulator. Research only; live-session REST traffic has priority."""
import sqlite3,threading,time
from datetime import datetime,timedelta
from collector import DB_PATH,KST,nh_call,collector,PROTECTED_CODES,regular_session
REGIME_PROXY_CODES=['069500','229200'];_LOCK=threading.Lock();_STOP=threading.Event();_THREAD=None
STATUS={'running':False,'pausedForLive':False,'startedAt':None,'finishedAt':None,'lastError':None,'targetDays':0,'targetCodes':0,'completedJobs':0,'totalJobs':0,'writtenBars':0,'skippedBars':0,'failedJobs':0,'currentCode':None,'currentDate':None,'requestedDays':20,'requestedCodes':40}
def _period_rows(p):
 if not isinstance(p,dict):return []
 for k in ('Output_1','output_1','Output_0','output_0'):
  if isinstance(p.get(k),list):return p[k]
 return []
def _trading_dates(days):
 out=[];d=datetime.now(KST).date()
 while len(out)<days:
  if d.weekday()<5:out.append(d)
  d-=timedelta(days=1)
 return out
def _existing_count(code,day):
 with sqlite3.connect(DB_PATH,timeout=10) as c:return int(c.execute("SELECT COUNT(*) FROM bars_5m WHERE code=? AND substr(bucket,1,10)=?",(code,day.strftime('%Y-%m-%d'))).fetchone()[0])
def _wait_for_research_window():
 while regular_session() and not _STOP.is_set():
  with _LOCK:STATUS['pausedForLive']=True
  time.sleep(30)
 with _LOCK:STATUS['pausedForLive']=False
 return not _STOP.is_set()
def _download_day(code,day):
 existing=_existing_count(code,day)
 # GOOD-data research requires at least 76 usable 5m bars. Partial 70~75 bar days are re-fetched.
 if existing>=76:return {'cached':True,'written':0,'skipped':existing,'received':existing}
 if not _wait_for_research_window():return {'cached':False,'written':0,'skipped':0,'received':0}
 payload=nh_call('/krstock/quote/v1/period',{'market_cd':'KRX','iem_cd':code,'edate':day.strftime('%Y%m%d'),'array_cnt':'120','gubun':'5','xtick':'5','today_cls_code':'1' if day==datetime.now(KST).date() else '0','fake_tick':'1'});rows=_period_rows(payload);written=skipped=0
 with sqlite3.connect(DB_PATH,timeout=10) as c:
  c.execute('PRAGMA journal_mode=WAL')
  for r in rows:
   ds=str(r.get('bsop_date') or '').strip();ts=str(r.get('bsop_time') or '').strip().zfill(6)
   if ds!=day.strftime('%Y%m%d'):skipped+=1;continue
   try:
    dt=datetime.strptime(ds+ts,'%Y%m%d%H%M%S').replace(tzinfo=KST);dt=dt.replace(minute=(dt.minute//5)*5,second=0,microsecond=0);hm=dt.hour*60+dt.minute;o=float(r.get('stck_oprc') or 0);h=float(r.get('stck_hgpr') or 0);l=float(r.get('stck_lwpr') or 0);cl=float(r.get('stck_prpr') or 0);v=int(float(r.get('vol') or 0))
   except Exception:skipped+=1;continue
   if not 540<=hm<=930 or min(o,h,l,cl)<=0:skipped+=1;continue
   c.execute('''INSERT INTO bars_5m(code,bucket,open,high,low,close,volume,sample_count) VALUES(?,?,?,?,?,?,?,0) ON CONFLICT(code,bucket) DO UPDATE SET open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,volume=excluded.volume,sample_count=0''',(code,dt.isoformat(),o,h,l,cl,v));written+=1
 return {'cached':False,'written':written,'skipped':skipped,'received':len(rows)}
def _worker(days,max_codes):
 try:
  collector.wait_for_universe(timeout=20);codes=[c for c in list(collector.watchlist) if c not in PROTECTED_CODES][:max_codes]
  for code in REGIME_PROXY_CODES:
   if code not in codes:codes.append(code)
  dates=_trading_dates(days)
  with _LOCK:STATUS.update({'running':True,'startedAt':datetime.now(KST).isoformat(),'finishedAt':None,'lastError':None,'targetDays':len(dates),'targetCodes':len(codes),'completedJobs':0,'totalJobs':len(dates)*len(codes),'writtenBars':0,'skippedBars':0,'failedJobs':0,'requestedDays':days,'requestedCodes':max_codes})
  for day in dates:
   for code in codes:
    if _STOP.is_set() or not _wait_for_research_window():return
    with _LOCK:STATUS.update({'currentCode':code,'currentDate':day.isoformat()})
    try:
     r=_download_day(code,day)
     with _LOCK:STATUS['writtenBars']+=r['written'];STATUS['skippedBars']+=r['skipped']
    except Exception as e:
     with _LOCK:STATUS['failedJobs']+=1;STATUS['lastError']=f'{code} {day}: {type(e).__name__}: {e}'[:700]
    finally:
     with _LOCK:STATUS['completedJobs']+=1
 finally:
  with _LOCK:STATUS.update({'running':False,'pausedForLive':False,'finishedAt':datetime.now(KST).isoformat(),'currentCode':None,'currentDate':None})
def start(days=20,max_codes=40):
 global _THREAD
 days=max(1,min(int(days),120));max_codes=max(1,min(int(max_codes),100))
 with _LOCK:
  if STATUS['running']:return {'ok':False,'message':'이미 과거 데이터 수집 중입니다.','status':dict(STATUS)}
  _STOP.clear();STATUS['running']=True
 _THREAD=threading.Thread(target=_worker,args=(days,max_codes),name='historical-5m-accumulator',daemon=True);_THREAD.start();return {'ok':True,'message':'과거 5분봉 수집 시작. 장중에는 자동 일시정지합니다.','status':status()}
def stop():_STOP.set();return {'ok':True,'message':'중지 요청됨','status':status()}
def status():
 with _LOCK:s=dict(STATUS)
 total=max(1,int(s.get('totalJobs') or 0));s['progressPct']=round(int(s.get('completedJobs') or 0)/total*100,1) if s.get('totalJobs') else 0;s['regimeProxyCodes']=list(REGIME_PROXY_CODES);s['liveSessionPriority']=True;s['cacheMinBars']=76;return s
