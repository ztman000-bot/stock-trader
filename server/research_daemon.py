"""Automatic research pipeline v0.17.3.
GOOD-data PF research + Stocks-in-Play + Exit Intelligence + Pullback Entry +
Public Strategy Benchmark + cached Market/Overnight labs + robust validation.
Research/data only. Never mutates Control/live rules and never sends orders.
"""
import json,sqlite3,threading,time
from datetime import datetime
from pathlib import Path
from market_lab import run_market_lab
from strategy_lab import run_lab,run_exit_lab
from profitability_lab import run_profitability_lab
from robust_validation import run_robust_validation
from benchmark_lab import run_benchmark_lab
from data_quality import audit as data_quality_audit
from stocks_in_play import scan as stocks_in_play_scan,snapshot_stats as stocks_snapshot_stats
from historical_accumulator import start as history_start,status as history_status
from collector import DB_PATH,collector,regular_session

OUT=Path(__file__).resolve().parent/'research_latest.json'
_LOCK=threading.Lock();_STARTED=False
_STATE={
    'running':False,'lastRun':None,'lastError':None,'intervalMin':60,
    'historyAuto':True,'historyDays':60,'historyCodes':40,'historyMinIntervalMin':180,
    'lastHistoryStart':None,'phase':'idle','liveSessionPriority':True,
    'goodDataGate':True,'stocksInPlayResearch':True,'exitIntelligenceV2':True,
    'pullbackEntryResearch':True,'publicBenchmarkLab':True,
    'cachedMarketLab':True,'cachedOvernightLab':True
}
OVERNIGHT_STRATEGIES=('cross_trend_v2','cross_trend','orb_cross','orb_rvol')

def _fmt(x,d=2):
    try:return f'{float(x):.{d}f}'
    except:return '-'

def _snapshot_universe():
    codes=list(dict.fromkeys(getattr(collector,'watchlist',[]) or []));now=datetime.now();day=now.date().isoformat()
    try:
        with sqlite3.connect(DB_PATH,timeout=10) as c:
            c.execute('CREATE TABLE IF NOT EXISTS universe_snapshots(snapshot_date TEXT NOT NULL,captured_at TEXT NOT NULL,code TEXT NOT NULL,source TEXT NOT NULL DEFAULT \'collector_watchlist\',PRIMARY KEY(snapshot_date,code))')
            for code in codes:
                c.execute('INSERT OR IGNORE INTO universe_snapshots(snapshot_date,captured_at,code,source) VALUES(?,?,?,?)',(day,now.isoformat(timespec='seconds'),str(code),'collector_watchlist'))
            total=c.execute('SELECT COUNT(*) FROM universe_snapshots').fetchone()[0]
            days=c.execute('SELECT COUNT(DISTINCT snapshot_date) FROM universe_snapshots').fetchone()[0]
        return {'ok':True,'todayCodes':len(codes),'snapshotRows':int(total),'snapshotDays':int(days)}
    except Exception as e:
        return {'ok':False,'error':f'{type(e).__name__}: {e}','todayCodes':len(codes)}

def _benchmark_summary(bm):
    rows=bm.get('benchmarks') or []
    if not rows:return '공개전략 벤치마크: 비교 가능한 GOOD 데이터가 아직 부족합니다.'
    parts=[]
    for x in rows:
        full=x.get('full') or {};lock=x.get('lockbox') or {};stress=x.get('lockboxStress') or {}
        parts.append(
            f"{x.get('name','-')} 전체PF {_fmt(full.get('profitFactor'))} / "
            f"LockboxPF {_fmt(lock.get('profitFactor'))} / StressPF {_fmt(stress.get('profitFactor'))}"
        )
    return '공개전략 동일조건 비교: '+' | '.join(parts)+'. 단순 공개형이 Lockbox/Stress에서 더 좋다면 복잡한 전략 추가효과는 아직 입증되지 않은 것으로 판단합니다.'

def _summary(p,r,q,hs,us,sip,ss,bm):
    b=p.get('best') or {};rd=p.get('readiness') or {};full=b.get('full') or {};oos=b.get('oos') or {};wf=r.get('walkForward') or {};lock=r.get('lockbox') or {};counts=q.get('counts') or {}
    top=(sip.get('rows') or [{}])[0] if sip else {}
    lines=[
        f"자동 연구 데이터 {hs.get('writtenBars',0):,}개 갱신, Universe Snapshot {us.get('snapshotDays',0)}일/{us.get('snapshotRows',0)}행. GOOD/PARTIAL/BAD={counts.get('GOOD',0)}/{counts.get('PARTIAL',0)}/{counts.get('BAD',0)}이며 PF 연구는 GOOD 데이터만 사용합니다.",
        f"Stocks-in-Play 시점기록 {ss.get('days',0)}일/{ss.get('rows',0)}행 축적 중. 현재 선두는 {top.get('name','-')}({top.get('code','-')}) 점수 {_fmt(top.get('score'))}이며 Cross 없이 독립적으로 기록합니다."
    ]
    if b:
        lines.append(
            f"PF 연구 선두: {b.get('strategy')} / {b.get('entryMode')} / {b.get('exit')} / {b.get('stocksInPlayMode')}. "
            f"전체 {full.get('trades',0)}건 PF {_fmt(full.get('profitFactor'))}, 기대값 {_fmt(full.get('expectancyPct'),3)}%, "
            f"평균익 {_fmt(full.get('avgWinPct'),3)}%/평균손 {_fmt(full.get('avgLossPct'),3)}%, payoff {_fmt(full.get('payoffRatio'),2)}, OOS PF {_fmt(oos.get('profitFactor'))}."
        )
    lines.append(_benchmark_summary(bm))
    lines.append(
        f"Walk-Forward {wf.get('folds',0)}구간 중 양수 {wf.get('positiveFolds',0)}구간. 개발에 사용하지 않은 Final Lockbox PF {_fmt(lock.get('profitFactor'))}, 기대값 {_fmt(lock.get('expectancyPct'),3)}%."
    )
    one=r.get('oneMinuteExitValidation') or {}
    lines.append('1분봉 Exit 검증: '+('완료' if one.get('ready') else '대기 — '+str(one.get('reason','1분봉 데이터 필요'))))
    lines.append(
        f"실전 준비도 {rd.get('score',0)}/100. 최종판정: {'강건성 연구 게이트 통과' if r.get('pass') else '실전 승격 금지'}. "
        "Control v0.8.0 LOCKED · 자동 전략변경 OFF · REAL ORDER OFF."
    )
    return '\n\n'.join(lines)

def _history_due():
    last=_STATE.get('lastHistoryStart')
    if not last:return True
    try:return (datetime.now()-datetime.fromisoformat(last)).total_seconds()>=_STATE['historyMinIntervalMin']*60
    except:return True

def _accumulate_and_wait():
    hs=history_status()
    if regular_session():
        _STATE['phase']='live-session-history-deferred';return hs
    _STATE['phase']='historical-accumulation'
    if not hs.get('running') and _history_due():
        r=history_start(_STATE['historyDays'],_STATE['historyCodes']);_STATE['lastHistoryStart']=datetime.now().isoformat(timespec='seconds')
        if not r.get('ok'):return history_status()
    elif not hs.get('running'):
        return hs
    deadline=time.time()+45*60
    while time.time()<deadline:
        if regular_session():return history_status()
        hs=history_status()
        if not hs.get('running'):return hs
        time.sleep(10)
    return history_status()

def _overnight_cache():
    out={}
    for strategy in OVERNIGHT_STRATEGIES:
        out[strategy]=run_exit_lab(strategy)
    return out

def run_once():
    with _LOCK:
        if _STATE['running']:return {'ok':False,'error':'research already running'}
        _STATE['running']=True
    try:
        hs=_accumulate_and_wait() if _STATE['historyAuto'] else history_status()
        if hs.get('running') and not hs.get('pausedForLive') and not regular_session():
            _STATE.update({'phase':'history-running-research-deferred','lastError':None})
            return {'ok':True,'deferred':True,'reason':'historical DB is still being updated; research waits for a stable snapshot','history':hs}
        _STATE['phase']='universe-snapshot';us=_snapshot_universe()
        _STATE['phase']='stocks-in-play';sip=stocks_in_play_scan(20);ss=stocks_snapshot_stats()
        _STATE['phase']='data-quality';q=data_quality_audit()
        _STATE['phase']='strategy-backtest';s=run_lab(40)
        _STATE['phase']='market-research';m=run_market_lab(80)
        _STATE['phase']='overnight-research';overnight=_overnight_cache()
        _STATE['phase']='pf-entry-exit-research';p=run_profitability_lab(40)
        _STATE['phase']='robust-validation';r=run_robust_validation(40)
        _STATE['phase']='public-strategy-benchmark';bm=run_benchmark_lab(40,p,r)
        data={
            'ok':True,'generatedAt':datetime.now().isoformat(timespec='seconds'),
            'summary':_summary(p,r,q,hs,us,sip,ss,bm),'history':hs,'dataQuality':q,
            'universeSnapshot':us,'stocksInPlay':sip,'stocksInPlaySnapshots':ss,
            'market':m,'overnight':overnight,'strategy':s,'profitability':p,'robustValidation':r,'benchmark':bm,
            'pipeline':[
                'stable-history-snapshot','universe-snapshot','stocks-in-play-point-in-time',
                'GOOD-data-gate','strategy-backtest','market-research','overnight-research-cache',
                'exit-intelligence-v2','pullback-entry','payoff-analysis','walk-forward','final-lockbox',
                'cost-fill-stress','public-strategy-benchmark'
            ],
            'safety':{
                'control':'v0.8.0 LOCKED','liveMutation':False,'realOrder':False,
                'liveSessionPriority':True,'qualityGate':'GOOD_ONLY','benchmarkAutoPromotion':False,
                'manualLabHeavyRun':False
            }
        }
        OUT.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
        _STATE.update({'lastRun':data['generatedAt'],'lastError':None,'phase':'idle'})
        return data
    except Exception as e:
        _STATE.update({'lastError':f'{type(e).__name__}: {e}','phase':'error'})
        return {'ok':False,'error':_STATE['lastError']}
    finally:
        _STATE['running']=False

def latest():
    if OUT.exists():
        try:return json.loads(OUT.read_text(encoding='utf-8'))
        except:pass
    return {'ok':True,'generatedAt':None,'summary':'자동 연구 준비 중','safety':{'control':'v0.8.0 LOCKED','liveMutation':False,'realOrder':False}}

def status():return dict(_STATE)

def _loop():
    time.sleep(20)
    while True:
        run_once();time.sleep(_STATE['intervalMin']*60)

def start():
    global _STARTED
    if _STARTED:return
    _STARTED=True
    threading.Thread(target=_loop,daemon=True,name='research-daemon').start()
