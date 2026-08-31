import math
import sqlite3
import json
from datetime import datetime

from collector import DB_PATH,FOCUS_SIZE,PROTECTED_CODES,active_candidates,bars,candidate_meta,collector,instrument_meta,is_safe_code,latest_quotes,universe_verified
KST=__import__('zoneinfo').ZoneInfo('Asia/Seoul')
INITIAL_CAPITAL=10_000_000.0;RISK_PER_TRADE=.0035;STOP_PCT=.010;TRAIL_ACTIVATE_PCT=.015;TRAIL_PCT=.008;BREAKEVEN_ACTIVATE_PCT=.008
MAX_CONSECUTIVE_LOSSES=2;MAX_OPEN_POSITIONS=2;MAX_DAILY_TRADES=8;DAILY_MAX_LOSS_PCT=.0075;MIN_BARS=35;MIN_SESSION_BARS=6;BUY_SCORE=78
STRONG_VOLUME_RATIO=1.80;MIN_VOLUME_RATIO=1.20;MIN_RSI=55;MAX_RSI=78;MIN_ADX=22;MIN_MARKET_BREADTH=.35;STRONG_OVERRIDE_SCORE=90
COMMISSION_RATE=.0001;SELL_TAX_RATE=.0015;SLIPPAGE_RATE=.0005;ROUND_TRIP_COST_EST=2*COMMISSION_RATE+SELL_TAX_RATE+2*SLIPPAGE_RATE;BREAKEVEN_BUFFER_PCT=max(.0035,ROUND_TRIP_COST_EST+.0005)
def _conn():
 c=sqlite3.connect(DB_PATH,timeout=10);c.row_factory=sqlite3.Row;c.execute('PRAGMA journal_mode=WAL');return c
def init_paper_db():
 with _conn() as c:
  c.executescript('''CREATE TABLE IF NOT EXISTS paper_trades(id INTEGER PRIMARY KEY AUTOINCREMENT,code TEXT NOT NULL,entry_at TEXT NOT NULL,entry_price REAL NOT NULL,qty INTEGER NOT NULL,score REAL NOT NULL,reasons TEXT,exit_at TEXT,exit_price REAL,exit_reason TEXT,pnl REAL,pnl_pct REAL,status TEXT NOT NULL DEFAULT 'OPEN',peak_price REAL);CREATE TABLE IF NOT EXISTS paper_state(key TEXT PRIMARY KEY,value TEXT NOT NULL);CREATE TABLE IF NOT EXISTS paper_signals(id INTEGER PRIMARY KEY AUTOINCREMENT,code TEXT NOT NULL,at TEXT NOT NULL,score REAL NOT NULL,action TEXT NOT NULL,reasons TEXT,shadow INTEGER NOT NULL DEFAULT 0);''')
  cols={r['name'] for r in c.execute('PRAGMA table_info(paper_trades)')}
  additions={'peak_price':'REAL','trough_price':'REAL','entry_snapshot':'TEXT','mfe_pct':'REAL','mae_pct':'REAL','failure_type':'TEXT'}
  for n,t in additions.items():
   if n not in cols:c.execute(f'ALTER TABLE paper_trades ADD COLUMN {n} {t}')
def _ema(v,p):
 if not v:return None
 a=2/(p+1);o=float(v[0])
 for x in v[1:]:o=a*float(x)+(1-a)*o
 return o
def _rsi(v,p=14):
 if len(v)<=p:return None
 ds=[b-a for a,b in zip(v[-p-1:-1],v[-p:])];g=sum(max(x,0) for x in ds)/p;l=sum(max(-x,0) for x in ds)/p
 return 100 if l==0 else 100-100/(1+g/l)
def _wilder_adx(rows,p=14):
 if len(rows)<p*2+1:return None,None,None
 trs=[];pd=[];md=[]
 for a,b in zip(rows[:-1],rows[1:]):
  ph,pl,pc=float(a['high']),float(a['low']),float(a['close']);ch,cl=float(b['high']),float(b['low']);up,down=ch-ph,pl-cl;pd.append(up if up>down and up>0 else 0);md.append(down if down>up and down>0 else 0);trs.append(max(ch-cl,abs(ch-pc),abs(cl-pc)))
 tr,ps,ms=sum(trs[:p]),sum(pd[:p]),sum(md[:p]);dx=[];pdi=mdi=0
 for i in range(p,len(trs)):
  if i>p:tr=tr-tr/p+trs[i];ps=ps-ps/p+pd[i];ms=ms-ms/p+md[i]
  pdi,mdi=(100*ps/tr,100*ms/tr) if tr>0 else (0,0);den=pdi+mdi;dx.append(100*abs(pdi-mdi)/den if den else 0)
 if len(dx)<p:return None,pdi,mdi
 adx=sum(dx[:p])/p
 for x in dx[p:]:adx=((adx*(p-1))+x)/p
 return adx,pdi,mdi
def _vwap(r):
 tv=sum(max(0,int(x['volume'])) for x in r);return None if tv<=0 else sum(((float(x['high'])+float(x['low'])+float(x['close']))/3)*max(0,int(x['volume'])) for x in r)/tv
def _parse_bucket(x):return datetime.fromisoformat(str(x)).astimezone(KST)
def _completed_rows(code):
 rows=bars(code,220);now=datetime.now(KST);cb=now.replace(minute=now.minute//5*5,second=0,microsecond=0);return [r for r in rows if 540<=(_parse_bucket(r['bucket']).hour*60+_parse_bucket(r['bucket']).minute)<=930 and _parse_bucket(r['bucket'])<cb]
def indicators(code):
 rows=_completed_rows(code)
 if len(rows)<MIN_BARS:return {'ready':False,'bars':len(rows),'need':MIN_BARS,'sessionBars':0}
 today=datetime.now(KST).date();session=[r for r in rows if _parse_bucket(r['bucket']).date()==today];cr=rows[-120:];cl=[float(r['close']) for r in cr];e9=_ema(cl[-60:],9);e20=_ema(cl[-80:],20);rsi=_rsi(cl);adx,pdi,mdi=_wilder_adx(cr);last,prev=cr[-1],cr[-2];price=float(last['close']);vw=_vwap(session) if session else _vwap(cr);prior=cr[-7:-1];rh=max(float(r['high']) for r in prior);av=sum(int(r['volume']) for r in prior)/len(prior);vr=int(last['volume'])/av if av>0 else 0;sr=len(session)>=MIN_SESSION_BARS;op=session[:MIN_SESSION_BARS] if sr else [];oh=max((float(r['high']) for r in op),default=None);ol=min((float(r['low']) for r in op),default=None);orb=bool(oh and price>oh);rb=price>rh;above=bool(vw and price>vw);bull=e9 is not None and e20 is not None and e9>e20;rok=rsi is not None and MIN_RSI<=rsi<=MAX_RSI;dok=adx is not None and adx>=MIN_ADX and pdi is not None and mdi is not None and pdi>mdi;vu=vr>=MIN_VOLUME_RATIO;sv=vr>=STRONG_VOLUME_RATIO;score=(5 if sr else 0)+(15 if above else 0)+(15 if bull else 0)+(max(0,min(15,(vr-1)*18.75)) if vr>1 else 0)+(20 if orb else 10 if rb else 0)+(10 if rok else 0)+(min(15,8+max(0,adx-MIN_ADX)*.35) if dok else 0);checks={'session_ready':sr,'above_vwap':above,'ema_bull':bull,'volume_up':vu,'strong_volume':sv,'orb_breakout':orb,'rolling_breakout':rb,'rsi_ok':rok,'dmi_ok':dok,'trend_gate':above and bull,'trigger_gate':orb or (rb and sv)};return {'ready':True,'bars':len(rows),'sessionBars':len(session),'price':price,'vwap':vw,'ema9':e9,'ema20':e20,'rsi':rsi,'adx':adx,'plusDI':pdi,'minusDI':mdi,'volumeRatio':vr,'rollingHigh':rh,'openingRangeHigh':oh,'openingRangeLow':ol,'checks':checks,'score':round(min(90,score),2),'bucket':last['bucket'],'previousClose':float(prev['close']),'signalBarComplete':True}
def _today_prefix():return datetime.now(KST).date().isoformat()
def daily_stats():
 init_paper_db()
 with _conn() as c:closed=c.execute("SELECT * FROM paper_trades WHERE status='CLOSED' AND exit_at LIKE ? ORDER BY id",(_today_prefix()+'%',)).fetchall()
 pnl=sum(float(r['pnl'] or 0) for r in closed);con=0
 for r in reversed(closed):
  if float(r['pnl'] or 0)<0:con+=1
  else:break
 ll=-INITIAL_CAPITAL*DAILY_MAX_LOSS_PCT;return {'date':_today_prefix(),'closedTrades':len(closed),'pnl':round(pnl,2),'consecutiveLosses':con,'lossLimit':round(ll,2),'lossLimitHit':pnl<=ll,'maxDailyTrades':MAX_DAILY_TRADES,'locked':con>=MAX_CONSECUTIVE_LOSSES or pnl<=ll or len(closed)>=MAX_DAILY_TRADES}
def open_positions():
 init_paper_db()
 with _conn() as c:return [dict(r) for r in c.execute("SELECT * FROM paper_trades WHERE status='OPEN' ORDER BY id")]
def recent_trades(limit=20):
 init_paper_db();limit=max(1,min(int(limit),100))
 with _conn() as c:return [dict(r) for r in c.execute("SELECT * FROM paper_trades ORDER BY id DESC LIMIT ?",(limit,))]
def _evaluate_base(code,market=None,stats=None):
 stats=stats or daily_stats();market=market or candidate_meta(code);name=market.get('name') or instrument_meta(code).get('name') or code;ind=indicators(code)
 if code in PROTECTED_CODES:return {'code':code,'name':name,'action':'PROTECTED','score':0,'indicators':ind,'market':market,'daily':stats}
 if not universe_verified():return {'code':code,'name':name,'action':'SAFETY_WAIT','score':0,'indicators':ind,'market':market,'daily':stats,'blockedReasons':['종목마스터 안전검증 대기']}
 if not is_safe_code(code):return {'code':code,'name':name,'action':'BLOCKED','score':0,'indicators':ind,'market':market,'daily':stats,'blockedReasons':['안전필터 제외 종목']}
 if not ind.get('ready'):return {'code':code,'name':name,'action':'WAIT_DATA','score':0,'indicators':ind,'market':market,'daily':stats}
 c=ind['checks'];liq=bool(market.get('liquidityOk'));score=round(min(100,float(ind['score'])+min(10,max(0,float(market.get('activityScore') or 0)/10))),2);blocked=[]
 if not c['session_ready']:blocked.append('개장30분 미완료')
 if not liq:blocked.append('유동성/거래대금/스프레드 기준 미달')
 if not c['trend_gate']:blocked.append('VWAP·EMA 추세 미충족')
 if not c['trigger_gate']:blocked.append('ORB30 또는 강한 돌파 미발생')
 if not c['rsi_ok']:blocked.append('RSI 범위 밖')
 if not c['dmi_ok']:blocked.append('ADX/DMI 약함')
 gate=c['session_ready'] and liq and c['trend_gate'] and c['trigger_gate'] and c['rsi_ok'] and c['dmi_ok'];action='BUY_CANDIDATE' if gate and score>=BUY_SCORE else 'SETUP' if c['session_ready'] and liq and c['trend_gate'] and score>=62 else 'WATCH';return {'code':code,'name':name,'action':action,'score':score,'reasons':[k for k,v in c.items() if v]+(['liquidity_ok'] if liq else []),'blockedReasons':blocked,'indicators':ind,'market':market,'daily':stats}
def evaluate(code):return _evaluate_base(code)
def scan():
 stats=daily_stats();evs=[_evaluate_base(m['code'],m,stats) for m in active_candidates(FOCUS_SIZE)];ready=[e for e in evs if e.get('indicators',{}).get('ready') and e.get('indicators',{}).get('checks',{}).get('session_ready')];breadth=sum(1 for e in ready if e['indicators']['checks'].get('trend_gate'))/len(ready) if ready else 0
 for e in evs:
  e['marketBreadth']=round(breadth,3)
  if e['action']=='BUY_CANDIDATE' and breadth<MIN_MARKET_BREADTH and e['score']<STRONG_OVERRIDE_SCORE:e['action']='SETUP';e.setdefault('blockedReasons',[]).append('시장 breadth 약함')
  if stats['locked'] and e['action']=='BUY_CANDIDATE':e['action']='SHADOW_ONLY'
 evs.sort(key=lambda e:(float(e.get('score') or 0),float((e.get('market') or {}).get('activityScore') or 0)),reverse=True);return evs
def _snapshot(ev):
 i=ev.get('indicators') or {};m=ev.get('market') or {};return json.dumps({'score':ev.get('score'),'rsi':i.get('rsi'),'adx':i.get('adx'),'plusDI':i.get('plusDI'),'minusDI':i.get('minusDI'),'volumeRatio':i.get('volumeRatio'),'vwap':i.get('vwap'),'ema9':i.get('ema9'),'ema20':i.get('ema20'),'orbHigh':i.get('openingRangeHigh'),'turnoverEok':m.get('turnoverEok'),'activityScore':m.get('activityScore'),'spreadPct':m.get('spreadPct'),'marketBreadth':ev.get('marketBreadth'),'bucket':i.get('bucket')},ensure_ascii=False)
def paper_enter(code,capital=INITIAL_CAPITAL,evaluation=None):
 init_paper_db();ev=evaluation or next((e for e in scan() if e['code']==code),_evaluate_base(code))
 if ev['action'] not in ('BUY_CANDIDATE','SHADOW_ONLY'):return {'ok':False,'message':'진입 조건 미충족','evaluation':ev}
 shadow=ev['action']=='SHADOW_ONLY';price=float(ev['indicators']['price']);capital=max(0,float(capital));qty=max(0,min(math.floor(capital*RISK_PER_TRADE/(price*(STOP_PCT+ROUND_TRIP_COST_EST))),math.floor(capital/(price*(1+COMMISSION_RATE+SLIPPAGE_RATE))))) if price>0 else 0
 if qty<1:return {'ok':False,'message':'가용 자본 대비 주문 가능 수량이 0입니다.','evaluation':ev}
 reasons=','.join(ev.get('reasons',[]));now=datetime.now(KST).isoformat()
 with _conn() as c:
  c.execute('INSERT INTO paper_signals(code,at,score,action,reasons,shadow) VALUES(?,?,?,?,?,?)',(code,now,ev['score'],ev['action'],reasons,1 if shadow else 0))
  if shadow:return {'ok':True,'shadow':True,'message':'Daily Lock: 신호만 기록','evaluation':ev}
  if c.execute("SELECT COUNT(*) n FROM paper_trades WHERE status='OPEN'").fetchone()['n']>=MAX_OPEN_POSITIONS:return {'ok':False,'message':'동시 포지션 제한','evaluation':ev}
  if c.execute("SELECT 1 FROM paper_trades WHERE code=? AND status='OPEN'",(code,)).fetchone():return {'ok':False,'message':'이미 열린 Paper 포지션','evaluation':ev}
  fill=price*(1+SLIPPAGE_RATE);cur=c.execute("INSERT INTO paper_trades(code,entry_at,entry_price,qty,score,reasons,status,peak_price,trough_price,entry_snapshot) VALUES(?,?,?,?,?,?,'OPEN',?,?,?)",(code,now,fill,qty,ev['score'],reasons,fill,fill,_snapshot(ev)));tid=cur.lastrowid
 collector.set_priority_codes([p['code'] for p in open_positions()]);return {'ok':True,'shadow':False,'tradeId':tid,'code':code,'price':fill,'qty':qty,'stopPrice':fill*(1-STOP_PCT),'breakevenProtectPrice':fill*(1+BREAKEVEN_BUFFER_PCT),'trailActivatePrice':fill*(1+TRAIL_ACTIVATE_PCT),'evaluation':ev}
def _failure(reason,pnl,mfe,mae,snap):
 if pnl>=0:return 'WIN'
 if reason=='STOP_LOSS' and mfe>=.8:return 'GAVE_BACK_PROFIT'
 if reason=='STOP_LOSS' and snap.get('volumeRatio',9)<1.5:return 'LOW_VOLUME_BREAKOUT'
 if snap.get('rsi',0)>72:return 'OVERBOUGHT_ENTRY'
 if snap.get('adx',99)<25:return 'WEAK_TREND'
 if reason=='EOD_EXIT':return 'NO_FOLLOW_THROUGH'
 return 'FALSE_BREAKOUT'
def close_position(trade_id,market_price,reason='MANUAL'):
 init_paper_db();market=float(market_price)
 if market<=0:return None
 with _conn() as c:
  p=c.execute("SELECT * FROM paper_trades WHERE id=? AND status='OPEN'",(trade_id,)).fetchone()
  if not p:return None
  entry=float(p['entry_price']);qty=int(p['qty']);peak=max(float(p['peak_price'] or entry),market);trough=min(float(p['trough_price'] or entry),market);exit_fill=market*(1-SLIPPAGE_RATE);gross=(exit_fill-entry)*qty;fees=entry*qty*COMMISSION_RATE+exit_fill*qty*(COMMISSION_RATE+SELL_TAX_RATE);pnl=gross-fees;pp=pnl/(entry*qty)*100 if entry*qty else 0;mfe=(peak/entry-1)*100;mae=(trough/entry-1)*100;snap=json.loads(p['entry_snapshot'] or '{}');ft=_failure(reason,pnl,mfe,mae,snap);now=datetime.now(KST).isoformat();c.execute("UPDATE paper_trades SET exit_at=?,exit_price=?,exit_reason=?,pnl=?,pnl_pct=?,status='CLOSED',peak_price=?,trough_price=?,mfe_pct=?,mae_pct=?,failure_type=? WHERE id=?",(now,exit_fill,reason,pnl,pp,peak,trough,mfe,mae,ft,trade_id))
 collector.set_priority_codes([p['code'] for p in open_positions()]);return {'id':trade_id,'code':p['code'],'reason':reason,'pnl':round(pnl,2),'pnlPct':round(pp,4),'mfePct':round(mfe,3),'maePct':round(mae,3),'failureType':ft,'exitPrice':exit_fill}
def force_close_all(reason='EOD_EXIT'):
 latest={r['code']:float(r['price']) for r in latest_quotes()};out=[]
 for p in open_positions():
  if latest.get(p['code']):
   x=close_position(p['id'],latest[p['code']],reason)
   if x:out.append(x)
 collector.set_priority_codes([]);return out
def mark_positions():
 ps=open_positions();collector.set_priority_codes([p['code'] for p in ps]);latest={r['code']:float(r['price']) for r in latest_quotes([p['code'] for p in ps])};closed=[]
 for p in ps:
  market=latest.get(p['code'])
  if not market:continue
  entry=float(p['entry_price']);peak=max(float(p['peak_price'] or entry),market);trough=min(float(p['trough_price'] or entry),market)
  with _conn() as c:c.execute('UPDATE paper_trades SET peak_price=?,trough_price=? WHERE id=?',(peak,trough,p['id']))
  reason='STOP_LOSS' if market<=entry*(1-STOP_PCT) else 'TRAILING_STOP' if peak>=entry*(1+TRAIL_ACTIVATE_PCT) and market<=peak*(1-TRAIL_PCT) else 'COST_COVER_PROTECT' if peak>=entry*(1+BREAKEVEN_ACTIVATE_PCT) and market<=entry*(1+BREAKEVEN_BUFFER_PCT) else None
  if reason:
   x=close_position(p['id'],market,reason)
   if x:closed.append(x)
 collector.set_priority_codes([p['code'] for p in open_positions()]);return {'closed':closed,'daily':daily_stats(),'open':open_positions()}
def validation_stats(limit=500):
 init_paper_db()
 with _conn() as c:rows=[dict(r) for r in c.execute("SELECT * FROM paper_trades WHERE status='CLOSED' ORDER BY id DESC LIMIT ?",(limit,))]
 wins=[r for r in rows if float(r.get('pnl') or 0)>0];loss=[r for r in rows if float(r.get('pnl') or 0)<0];gp=sum(float(r['pnl']) for r in wins);gl=abs(sum(float(r['pnl']) for r in loss));n=len(rows);equity=INITIAL_CAPITAL;peak=equity;mdd=0
 for r in reversed(rows):equity+=float(r.get('pnl') or 0);peak=max(peak,equity);mdd=max(mdd,(peak-equity)/peak*100 if peak else 0)
 buckets=[]
 for lo,hi,label in [(78,85,'78-84'),(85,90,'85-89'),(90,95,'90-94'),(95,101,'95+')]:
  a=[r for r in rows if lo<=float(r.get('score') or 0)<hi];buckets.append({'bucket':label,'trades':len(a),'winRate':round(100*sum(1 for r in a if float(r.get('pnl') or 0)>0)/len(a),1) if a else 0,'avgPnlPct':round(sum(float(r.get('pnl_pct') or 0) for r in a)/len(a),3) if a else 0})
 fails={}
 for r in loss:fails[r.get('failure_type') or 'UNCLASSIFIED']=fails.get(r.get('failure_type') or 'UNCLASSIFIED',0)+1
 return {'trades':n,'wins':len(wins),'losses':len(loss),'winRate':round(100*len(wins)/n,1) if n else 0,'netPnl':round(sum(float(r.get('pnl') or 0) for r in rows),2),'profitFactor':round(gp/gl,2) if gl else (999 if gp else 0),'expectancyPct':round(sum(float(r.get('pnl_pct') or 0) for r in rows)/n,3) if n else 0,'maxDrawdownPct':round(mdd,3),'avgMfePct':round(sum(float(r.get('mfe_pct') or 0) for r in rows)/n,3) if n else 0,'avgMaePct':round(sum(float(r.get('mae_pct') or 0) for r in rows)/n,3) if n else 0,'scoreBuckets':buckets,'failureTypes':fails,'controlStrategy':'v0.8.0 LOCKED','liveRuleAutoMutation':False}
init_paper_db()
