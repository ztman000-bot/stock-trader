"""Robust validation v0.17.8.
GOOD-only data, walk-forward development folds, untouched final lockbox, cost/fill stress,
and KR 1-minute Exit Replay validation. Research only; Control/live rules are never changed.
"""
from profitability_lab import (
    _candidates,_eval,FILTERS,EXIT_CONFIGS,REGIME_MODES,SURGE_MODES,
    ENTRY_MODES,PLAY_MODES,BASE_SLIPPAGE
)

try:
    from kr_1m_research import coverage as kr_1m_coverage
except Exception:
    kr_1m_coverage = None
try:
    from one_minute_exit_replay import validation_status as one_minute_replay_status
except Exception:
    one_minute_replay_status = None


def _date_slices(cands,folds=4):
    dates=sorted({x['date'] for x in cands});n=len(dates)
    if n<10:return [],set()
    lock_n=max(2,int(n*.20));dev=dates[:-lock_n];lock=set(dates[-lock_n:]);chunks=[]
    for k in range(folds):
        cut=max(3,int(len(dev)*(.45+.10*k)));test_end=min(len(dev),cut+max(1,int(len(dev)*.15)))
        if test_end>cut:chunks.append((set(dev[:cut]),set(dev[cut:test_end])))
    return chunks,lock


def _one_minute_status():
    base={'ready':False,'dataReady':False,'engineConnected':False,'bars':0,'completeDates':0,
          'completeCodeDays':0,'reason':'KR 1분봉 데이터/Exit Replay 검증을 준비 중입니다.'}
    if kr_1m_coverage is None:
        base['reason']='KR 1분봉 수집 모듈을 불러오지 못했습니다.'
        return base
    try:
        c=kr_1m_coverage() or {}
        base.update({'dataReady':bool(c.get('dataReady')),'bars':int(c.get('bars') or 0),
            'completeBars':int(c.get('completeBars') or 0),'days':int(c.get('days') or 0),
            'completeDates':int(c.get('completeDates') or 0),'completeCodeDays':int(c.get('completeCodeDays') or 0),
            'partialCodeDays':int(c.get('partialCodeDays') or 0),'dataReadyRule':c.get('dataReadyRule')})
    except Exception as exc:
        base['reason']=f'KR 1분봉 커버리지 확인 실패: {type(exc).__name__}: {exc}'
        return base
    if one_minute_replay_status is None:
        base['reason']='1분봉 데이터는 있으나 Exit Replay 모듈을 불러오지 못했습니다.'
        return base
    try:
        r=one_minute_replay_status() or {}
        base['engineConnected']=bool(r.get('engineConnected'))
        base['replay']=r
        base['ready']=bool(base['dataReady'] and r.get('validated'))
        if not base['dataReady']:
            base['reason']='1분봉 최소 데이터 커버리지를 축적 중입니다. Exit Replay는 연결되어 있습니다.'
        elif not r.get('validated'):
            base['reason']='1분봉 데이터 게이트는 충족했지만 Paper replay 표본/경로 일치 검증이 아직 부족합니다.'
        else:
            base['reason']='1분봉 데이터와 Exit Replay 검증 게이트를 모두 충족했습니다. 이는 연구 검증이며 실주문 승격을 의미하지 않습니다.'
        return base
    except Exception as exc:
        base['reason']=f'1분봉 Exit Replay 확인 실패: {type(exc).__name__}: {exc}'
        return base


def run_robust_validation(max_codes=40):
    cands=_candidates(max(10,min(int(max_codes),100)));folds,lockbox=_date_slices(cands);results=[]
    for train,test in folds:
        ranked=[]
        for f in FILTERS:
            for cfg in EXIT_CONFIGS:
                for rm in REGIME_MODES:
                    for sm in SURGE_MODES:
                        for em in ENTRY_MODES:
                            for pm in PLAY_MODES:
                                m=_eval(cands,f,cfg,rm,sm,train,entry_mode=em,play_mode=pm)
                                if m['trades']>=8:
                                    complexity=f['penalty']+cfg['penalty']+rm['penalty']+sm['penalty']+em['penalty']+pm['penalty']
                                    objective=m['expectancyPct']+max(-.05,min(.05,(m['profitFactor']-1)*.05))-complexity
                                    ranked.append((objective,m['profitFactor'],f,cfg,rm,sm,em,pm,m))
        ranked.sort(key=lambda x:(x[0],x[1]),reverse=True)
        if not ranked:continue
        _,_,f,cfg,rm,sm,em,pm,tr=ranked[0]
        te=_eval(cands,f,cfg,rm,sm,test,entry_mode=em,play_mode=pm)
        results.append({'train':tr,'test':te,'strategy':f['id'],'exit':cfg['id'],'market':rm['id'],
                        'surge':sm['id'],'entry':em['id'],'play':pm['id']})
    positive=sum(1 for x in results if x['test']['profitFactor']>1 and x['test']['expectancyPct']>0)
    if results:
        winner=max(results,key=lambda x:(x['test']['profitFactor']>1 and x['test']['expectancyPct']>0,x['test']['expectancyPct'],x['test']['profitFactor']))
        f=next(x for x in FILTERS if x['id']==winner['strategy']);cfg=next(x for x in EXIT_CONFIGS if x['id']==winner['exit'])
        rm=next(x for x in REGIME_MODES if x['id']==winner['market']);sm=next(x for x in SURGE_MODES if x['id']==winner['surge'])
        em=next(x for x in ENTRY_MODES if x['id']==winner['entry']);pm=next(x for x in PLAY_MODES if x['id']==winner['play'])
        lock=_eval(cands,f,cfg,rm,sm,lockbox,entry_mode=em,play_mode=pm)
        stress=_eval(cands,f,cfg,rm,sm,lockbox,BASE_SLIPPAGE*2,1,em,pm)
        selected={'strategy':f['id'],'exit':cfg['id'],'entry':em['id'],'market':rm['id'],'surge':sm['id'],'play':pm['id']}
    else:
        lock={'trades':0,'profitFactor':0,'expectancyPct':0,'winRate':0,'avgWinPct':0,'avgLossPct':0,'payoffRatio':0,'maxDrawdownPct':0}
        stress=dict(lock);selected=None
    research_pass=bool(results and positive>=max(2,len(results)-1) and lock['trades']>=10 and
        lock['profitFactor']>1 and lock['expectancyPct']>0 and stress['profitFactor']>=1 and stress['expectancyPct']>=0)
    one_min=_one_minute_status()
    return {'ok':True,'version':'0.17.8','researchOnly':True,'qualityGate':'GOOD_ONLY',
        'candidateTrades':len(cands),'selectedForLockbox':selected,
        'walkForward':{'folds':len(results),'positiveFolds':positive,'results':results},
        'lockbox':lock,'lockboxStress':stress,'oneMinuteExitValidation':one_min,
        'pass':research_pass,'deploymentReady':bool(research_pass and one_min.get('ready')),
        'gate':'GOOD data + walk-forward 반복 양수 + final lockbox 양수 + 2x slippage/1bar-late 방어',
        'deploymentGate':'research pass + KR 1m Exit Replay validation; NH simulation/micro-live는 별도 단계',
        'liveRuleAutoMutation':False,'realOrderEnabled':False}
