import math
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

from collector import DB_PATH, PROTECTED_CODES, bars, latest_quotes

KST = ZoneInfo('Asia/Seoul')
INITIAL_CAPITAL = 10_000_000.0
RISK_PER_TRADE = 0.0035
STOP_PCT = 0.010
TARGET_PCT = 0.015
TRAIL_ACTIVATE_PCT = 0.015
TRAIL_PCT = 0.008
BREAKEVEN_ACTIVATE_PCT = 0.008
BREAKEVEN_BUFFER_PCT = 0.001
MAX_CONSECUTIVE_LOSSES = 2
MIN_BARS = 35
BUY_SCORE = 75
STRONG_VOLUME_RATIO = 1.80
COMMISSION_RATE = 0.0001
SELL_TAX_RATE = 0.0015
SLIPPAGE_RATE = 0.0005


def _conn():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    return conn


def init_paper_db():
    with _conn() as conn:
        conn.executescript('''
        CREATE TABLE IF NOT EXISTS paper_trades (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          code TEXT NOT NULL,
          entry_at TEXT NOT NULL,
          entry_price REAL NOT NULL,
          qty INTEGER NOT NULL,
          score REAL NOT NULL,
          reasons TEXT,
          exit_at TEXT,
          exit_price REAL,
          exit_reason TEXT,
          pnl REAL,
          pnl_pct REAL,
          status TEXT NOT NULL DEFAULT 'OPEN',
          peak_price REAL
        );
        CREATE TABLE IF NOT EXISTS paper_state (key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS paper_signals (
          id INTEGER PRIMARY KEY AUTOINCREMENT,code TEXT NOT NULL,at TEXT NOT NULL,
          score REAL NOT NULL,action TEXT NOT NULL,reasons TEXT,shadow INTEGER NOT NULL DEFAULT 0
        );
        ''')
        cols={r['name'] for r in conn.execute('PRAGMA table_info(paper_trades)').fetchall()}
        if 'peak_price' not in cols: conn.execute('ALTER TABLE paper_trades ADD COLUMN peak_price REAL')


def _ema(values, period):
    if not values: return None
    alpha=2.0/(period+1); out=float(values[0])
    for v in values[1:]: out=alpha*float(v)+(1-alpha)*out
    return out


def _rsi(values, period=14):
    if len(values)<=period: return None
    gains=[]; losses=[]
    for a,b in zip(values[-period-1:-1],values[-period:]):
        d=b-a; gains.append(max(d,0)); losses.append(max(-d,0))
    ag=sum(gains)/period; al=sum(losses)/period
    if al==0: return 100.0
    return 100-100/(1+ag/al)


def _wilder_adx(rows, period=14):
    if len(rows)<period*2+1: return None,None,None
    trs=[]; pdms=[]; mdms=[]
    for prev,cur in zip(rows[:-1],rows[1:]):
        ph,pl,pc=float(prev['high']),float(prev['low']),float(prev['close']); ch,cl=float(cur['high']),float(cur['low'])
        up=ch-ph; down=pl-cl
        pdms.append(up if up>down and up>0 else 0.0); mdms.append(down if down>up and down>0 else 0.0)
        trs.append(max(ch-cl,abs(ch-pc),abs(cl-pc)))
    tr_s=sum(trs[:period]); pdm_s=sum(pdms[:period]); mdm_s=sum(mdms[:period]); dxs=[]; pdi=mdi=0.0
    for i in range(period,len(trs)):
        if i>period:
            tr_s=tr_s-tr_s/period+trs[i]; pdm_s=pdm_s-pdm_s/period+pdms[i]; mdm_s=mdm_s-mdm_s/period+mdms[i]
        if tr_s<=0: pdi=mdi=0.0
        else: pdi=100*pdm_s/tr_s; mdi=100*mdm_s/tr_s
        denom=pdi+mdi; dxs.append(100*abs(pdi-mdi)/denom if denom else 0.0)
    if len(dxs)<period: return None,pdi,mdi
    adx=sum(dxs[:period])/period
    for dx in dxs[period:]: adx=((adx*(period-1))+dx)/period
    return adx,pdi,mdi


def _vwap(rows):
    total_v=sum(max(0,int(r['volume'])) for r in rows)
    if total_v<=0: return None
    return sum(((float(r['high'])+float(r['low'])+float(r['close']))/3.0)*max(0,int(r['volume'])) for r in rows)/total_v


def _parse_bucket(value): return datetime.fromisoformat(str(value)).astimezone(KST)

def _completed_rows(code):
    rows=bars(code,200); now=datetime.now(KST); current_bucket=now.replace(minute=(now.minute//5)*5,second=0,microsecond=0)
    return [r for r in rows if _parse_bucket(r['bucket'])<current_bucket]


def indicators(code):
    rows=_completed_rows(code)
    if len(rows)<MIN_BARS: return {'ready':False,'bars':len(rows),'need':MIN_BARS}
    today=datetime.now(KST).date(); session=[r for r in rows if _parse_bucket(r['bucket']).date()==today]; calc_rows=rows[-120:]
    closes=[float(r['close']) for r in calc_rows]; ema9=_ema(closes[-60:],9); ema20=_ema(closes[-80:],20); rsi=_rsi(closes,14); adx,pdi,mdi=_wilder_adx(calc_rows,14)
    last=calc_rows[-1]; prev=calc_rows[-2]; price=float(last['close']); vwap=_vwap(session) if session else _vwap(calc_rows)
    prior6=calc_rows[-7:-1]; prev_high=max(float(r['high']) for r in prior6); avg_vol=sum(int(r['volume']) for r in prior6)/len(prior6); volume_ratio=int(last['volume'])/avg_vol if avg_vol>0 else 0.0
    breakout=price>prev_high; above_vwap=bool(vwap and price>vwap); ema_bull=ema9 is not None and ema20 is not None and ema9>ema20
    rsi_ok=rsi is not None and 52<=rsi<=75; dmi_ok=adx is not None and adx>=20 and pdi is not None and mdi is not None and pdi>mdi
    volume_up=volume_ratio>=1.20; strong_volume=volume_ratio>=STRONG_VOLUME_RATIO; trend_gate=above_vwap and ema_bull; trigger_gate=breakout or strong_volume
    score=0
    if above_vwap: score+=20
    if ema_bull: score+=20
    if volume_ratio>1: score+=max(0,min(15,(volume_ratio-1.0)*18.75))
    if breakout:
        breakout_pct=(price/prev_high-1)*100 if prev_high>0 else 0; score+=min(20,12+breakout_pct*80)
    if rsi_ok: score+=10
    if dmi_ok: score+=min(15,8+max(0,adx-20)*0.35)
    score=round(min(100,score),2)
    checks={'above_vwap':above_vwap,'ema_bull':ema_bull,'volume_up':volume_up,'strong_volume':strong_volume,'breakout':breakout,'rsi_ok':rsi_ok,'dmi_ok':dmi_ok,'trend_gate':trend_gate,'trigger_gate':trigger_gate}
    return {'ready':True,'bars':len(rows),'price':price,'vwap':vwap,'ema9':ema9,'ema20':ema20,'rsi':rsi,'adx':adx,'plusDI':pdi,'minusDI':mdi,'volumeRatio':volume_ratio,'previousHigh':prev_high,'checks':checks,'score':score,'bucket':last['bucket'],'previousClose':float(prev['close']),'signalBarComplete':True}


def _today_prefix(): return datetime.now(KST).date().isoformat()

def daily_stats():
    init_paper_db()
    with _conn() as conn: closed=conn.execute("SELECT * FROM paper_trades WHERE status='CLOSED' AND exit_at LIKE ? ORDER BY id",(_today_prefix()+'%',)).fetchall()
    pnl=sum(float(r['pnl'] or 0) for r in closed); consecutive=0
    for r in reversed(closed):
        if float(r['pnl'] or 0)<0: consecutive+=1
        else: break
    return {'date':_today_prefix(),'closedTrades':len(closed),'pnl':round(pnl,2),'consecutiveLosses':consecutive,'locked':consecutive>=MAX_CONSECUTIVE_LOSSES}


def open_positions():
    init_paper_db()
    with _conn() as conn: return [dict(r) for r in conn.execute("SELECT * FROM paper_trades WHERE status='OPEN' ORDER BY id").fetchall()]


def recent_trades(limit=20):
    init_paper_db(); limit=max(1,min(int(limit),100))
    with _conn() as conn: return [dict(r) for r in conn.execute("SELECT * FROM paper_trades ORDER BY id DESC LIMIT ?",(limit,)).fetchall()]


def evaluate(code):
    ind=indicators(code); stats=daily_stats()
    if code in PROTECTED_CODES: return {'code':code,'action':'PROTECTED','score':0,'indicators':ind,'daily':stats}
    if not ind.get('ready'): return {'code':code,'action':'WAIT_DATA','score':0,'indicators':ind,'daily':stats}
    score=ind['score']; c=ind['checks']
    if c['trend_gate'] and c['trigger_gate'] and score>=BUY_SCORE: action='BUY_CANDIDATE'
    elif c['trend_gate'] and (score>=60 or c['volume_up']): action='SETUP'
    else: action='WATCH'
    if stats['locked'] and action=='BUY_CANDIDATE': action='SHADOW_ONLY'
    return {'code':code,'action':action,'score':score,'reasons':[k for k,v in c.items() if v],'indicators':ind,'daily':stats}


def scan(): return [evaluate(q['code']) for q in latest_quotes()]


def paper_enter(code, capital=INITIAL_CAPITAL):
    init_paper_db(); ev=evaluate(code)
    if ev['action'] not in ('BUY_CANDIDATE','SHADOW_ONLY'): return {'ok':False,'message':'진입 조건 미충족','evaluation':ev}
    shadow=ev['action']=='SHADOW_ONLY'; price=float(ev['indicators']['price']); capital=max(0.0,float(capital)); risk_cash=capital*RISK_PER_TRADE
    risk_qty=math.floor(risk_cash/(price*STOP_PCT)) if price>0 else 0; cash_qty=math.floor(capital/(price*(1+COMMISSION_RATE+SLIPPAGE_RATE))) if price>0 else 0; qty=max(0,min(risk_qty,cash_qty))
    if qty<1: return {'ok':False,'message':'가용 자본 대비 주문 가능 수량이 0입니다.','evaluation':ev}
    reasons=','.join(ev.get('reasons',[])); now=datetime.now(KST).isoformat()
    with _conn() as conn:
        conn.execute('INSERT INTO paper_signals(code,at,score,action,reasons,shadow) VALUES(?,?,?,?,?,?)',(code,now,ev['score'],ev['action'],reasons,1 if shadow else 0))
        if shadow: return {'ok':True,'shadow':True,'message':'2연속 손실 잠금: 신호만 기록, 신규 Paper 진입 없음','evaluation':ev}
        if conn.execute("SELECT 1 FROM paper_trades WHERE code=? AND status='OPEN'",(code,)).fetchone(): return {'ok':False,'message':'이미 열린 Paper 포지션이 있습니다.','evaluation':ev}
        entry_fill=price*(1+SLIPPAGE_RATE); cur=conn.execute("INSERT INTO paper_trades(code,entry_at,entry_price,qty,score,reasons,status,peak_price) VALUES(?,?,?,?,?,?, 'OPEN',?)",(code,now,entry_fill,qty,ev['score'],reasons,entry_fill)); trade_id=cur.lastrowid
    return {'ok':True,'shadow':False,'tradeId':trade_id,'code':code,'price':entry_fill,'qty':qty,'stopPrice':entry_fill*(1-STOP_PCT),'targetPrice':entry_fill*(1+TARGET_PCT),'evaluation':ev}


def close_position(trade_id, market_price, reason='MANUAL'):
    init_paper_db(); market=float(market_price)
    if market<=0: return None
    with _conn() as conn:
        p=conn.execute("SELECT * FROM paper_trades WHERE id=? AND status='OPEN'",(trade_id,)).fetchone()
        if not p: return None
        entry=float(p['entry_price']); qty=int(p['qty']); peak=max(float(p['peak_price'] or entry),market); exit_fill=market*(1-SLIPPAGE_RATE)
        gross=(exit_fill-entry)*qty; fees=(entry*qty*COMMISSION_RATE)+(exit_fill*qty*(COMMISSION_RATE+SELL_TAX_RATE)); pnl=gross-fees; pnl_pct=pnl/(entry*qty)*100 if entry*qty else 0; now=datetime.now(KST).isoformat()
        conn.execute("UPDATE paper_trades SET exit_at=?,exit_price=?,exit_reason=?,pnl=?,pnl_pct=?,status='CLOSED',peak_price=? WHERE id=?",(now,exit_fill,reason,pnl,pnl_pct,peak,trade_id))
    return {'id':trade_id,'code':p['code'],'reason':reason,'pnl':round(pnl,2),'pnlPct':round(pnl_pct,4),'exitPrice':exit_fill}


def force_close_all(reason='EOD_EXIT'):
    latest={r['code']:float(r['price']) for r in latest_quotes()}; closed=[]
    for p in open_positions():
        market=latest.get(p['code'])
        if not market: continue
        result=close_position(p['id'],market,reason)
        if result: closed.append(result)
    return closed


def mark_positions():
    latest={r['code']:float(r['price']) for r in latest_quotes()}; closed=[]
    for p in open_positions():
        market=latest.get(p['code'])
        if not market: continue
        entry=float(p['entry_price']); peak=max(float(p['peak_price'] or entry),market)
        if peak!=float(p['peak_price'] or entry):
            with _conn() as conn: conn.execute('UPDATE paper_trades SET peak_price=? WHERE id=?',(peak,p['id']))
        reason=None
        # Hard loss cap stays mechanical. Once a trade proves itself, protect the
        # position near breakeven. At +1.5% we do not cut a strong trend short;
        # the trade graduates to an 0.8% peak trailing stop.
        if market<=entry*(1-STOP_PCT): reason='STOP_LOSS'
        elif peak>=entry*(1+TRAIL_ACTIVATE_PCT) and market<=peak*(1-TRAIL_PCT): reason='TRAILING_STOP'
        elif peak>=entry*(1+BREAKEVEN_ACTIVATE_PCT) and market<=entry*(1+BREAKEVEN_BUFFER_PCT): reason='BREAKEVEN_PROTECT'
        if reason:
            result=close_position(p['id'],market,reason)
            if result: closed.append(result)
    return {'closed':closed,'daily':daily_stats(),'open':open_positions()}


init_paper_db()
