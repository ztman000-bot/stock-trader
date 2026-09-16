"""Robust validation v0.17.16.
GOOD-only data, purged non-overlapping development folds, precommitted future holdout,
cost/fill stress, and KR 1-minute Exit Replay validation.
Research only; Control/live rules are never changed.
"""
from math import ceil
from datetime import datetime
from zoneinfo import ZoneInfo
import research_experiments as experiments

from profitability_lab import (
    _candidates,_eval,FILTERS,EXIT_CONFIGS,REGIME_MODES,SURGE_MODES,
    ENTRY_MODES,PLAY_MODES,BASE_SLIPPAGE,COMMISSION,SELL_TAX,_metrics,available_codes
)

try:
    from kr_1m_research import coverage as kr_1m_coverage
except Exception:
    kr_1m_coverage = None
try:
    from one_minute_exit_replay import validation_status as one_minute_replay_status
except Exception:
    one_minute_replay_status = None

WF_FOLDS=4
WF_PURGE_DAYS=1
WF_MIN_TRAIN_TRADES=12
LOCKBOX_MIN_TRADES=20


def _date_slices(cands,folds=WF_FOLDS,purge_days=WF_PURGE_DAYS):
    """Expanding train windows with disjoint forward tests and a purge gap.

    Test dates are never reused by another test fold. The last ~20% is reserved
    within development and never appears in these folds. It is not final evidence;
    the separate experiment registry precommits an actually future test window.
    """
    dates=sorted({x['date'] for x in cands});n=len(dates)
    if n<15:return [],set()
    lock_n=max(3,int(n*.20));dev=dates[:-lock_n];lock=set(dates[-lock_n:])
    if len(dev)<8:return [],lock
    min_train=max(5,int(len(dev)*.40));cursor=min_train;chunks=[]
    while len(chunks)<max(1,int(folds)) and cursor<len(dev):
        test_start=min(len(dev),cursor+max(0,int(purge_days)))
        if test_start>=len(dev):break
        folds_left=max(1,int(folds)-len(chunks));available=len(dev)-test_start
        test_len=max(1,ceil(available/folds_left));test_end=min(len(dev),test_start+test_len)
        train=set(dev[:cursor]);test=set(dev[test_start:test_end])
        if train and test:chunks.append((train,test))
        cursor=test_end
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


def run_robust_validation(max_codes=40, *, registry_path=None, today=None):
    today=today or datetime.now(ZoneInfo('Asia/Seoul')).date()
    max_codes=max(10,min(int(max_codes),100))
    record=experiments.active(registry_path)
    scope=experiments.candidate_scope(registry_path)
    codes=scope.get('codes') if record else [x['code'] for x in available_codes()[:max_codes]]
    scope['codes']=codes
    all_cands=[x for x in _candidates(max_codes,**scope) if str(x['date'])<today.isoformat()]
    cands=experiments.development_candidates(all_cands,registry_path)
    folds,development_holdout=_date_slices(cands);results=[]
    settings={'maxCodes':max_codes,'universeCodes':codes,'filters':FILTERS,'exits':EXIT_CONFIGS,'regimes':REGIME_MODES,
              'surges':SURGE_MODES,'entries':ENTRY_MODES,'plays':PLAY_MODES,
              'commission':COMMISSION,'sellTax':SELL_TAX,'slippage':BASE_SLIPPAGE,
              'folds':WF_FOLDS,'purgeDays':WF_PURGE_DAYS,'minTrainTrades':WF_MIN_TRAIN_TRADES,
              'minFinalTrades':LOCKBOX_MIN_TRADES}
    for fold_no,(train,test) in enumerate(folds,1):
        ranked=[]
        for f in FILTERS:
            for cfg in EXIT_CONFIGS:
                for rm in REGIME_MODES:
                    for sm in SURGE_MODES:
                        for em in ENTRY_MODES:
                            for pm in PLAY_MODES:
                                m=_eval(cands,f,cfg,rm,sm,train,entry_mode=em,play_mode=pm)
                                if m['trades']>=WF_MIN_TRAIN_TRADES:
                                    complexity=f['penalty']+cfg['penalty']+rm['penalty']+sm['penalty']+em['penalty']+pm['penalty']
                                    objective=m['expectancyPct']+max(-.05,min(.05,(m['profitFactor']-1)*.05))-complexity
                                    ranked.append((objective,m['profitFactor'],f,cfg,rm,sm,em,pm,m))
        ranked.sort(key=lambda x:(x[0],x[1]),reverse=True)
        if not ranked:continue
        _,_,f,cfg,rm,sm,em,pm,tr=ranked[0]
        te=_eval(cands,f,cfg,rm,sm,test,entry_mode=em,play_mode=pm)
        train_dates=sorted(train);test_dates=sorted(test)
        results.append({'fold':fold_no,'train':tr,'test':te,'trainDays':len(train),'testDays':len(test),
                        'trainStart':str(train_dates[0]),'trainEnd':str(train_dates[-1]),
                        'testStart':str(test_dates[0]),'testEnd':str(test_dates[-1]),
                        'strategy':f['id'],'exit':cfg['id'],'market':rm['id'],
                        'surge':sm['id'],'entry':em['id'],'play':pm['id']})
    positive=sum(1 for x in results if x['test']['profitFactor']>1 and x['test']['expectancyPct']>0)
    required_positive=max(2,ceil(len(results)*.75)) if results else 0
    selection=None
    if results:
        # Each fold selects on train only. Final candidate is the latest train
        # winner, never the winner with the best test return across folds.
        winner=results[-1]
        f=next(x for x in FILTERS if x['id']==winner['strategy']);cfg=next(x for x in EXIT_CONFIGS if x['id']==winner['exit'])
        rm=next(x for x in REGIME_MODES if x['id']==winner['market']);sm=next(x for x in SURGE_MODES if x['id']==winner['surge'])
        em=next(x for x in ENTRY_MODES if x['id']==winner['entry']);pm=next(x for x in PLAY_MODES if x['id']==winner['play'])
        selection={'filter':f,'exit':cfg,'regime':rm,'surge':sm,'entry':em,'play':pm}
        if record is None and len(results)>=3:
            record=experiments.freeze(all_cands,selection,settings,path=registry_path,today=today)
    lock=_metrics([]);stress=_metrics([])
    experiment={'status':'INSUFFICIENT_DEVELOPMENT_DATA','finalEvidence':False}
    selected=None
    if record:
        selection=record['manifest']['selection']
        f,cfg,rm,sm,em,pm=(selection[k] for k in ('filter','exit','regime','surge','entry','play'))
        selected={'strategy':f['id'],'exit':cfg['id'],'entry':em['id'],'market':rm['id'],'surge':sm['id'],'play':pm['id']}
        def final_evaluator(manifest):
            dates={x['date'] for x in all_cands if manifest['lockboxStart']<=str(x['date'])<=manifest['lockboxEnd']}
            return {'lockbox':_eval(all_cands,f,cfg,rm,sm,dates,entry_mode=em,play_mode=pm,with_account=True),
                    'lockboxStress':_eval(all_cands,f,cfg,rm,sm,dates,BASE_SLIPPAGE*2,1,em,pm,with_account=True)}
        experiment=experiments.evaluate_once(record,all_cands,settings,final_evaluator,path=registry_path,today=today)
        if experiment.get('finalEvidence'):
            lock=experiment['result']['lockbox'];stress=experiment['result']['lockboxStress']
    research_pass=bool(experiment.get('finalEvidence') and len(results)>=3 and positive>=required_positive and lock['trades']>=LOCKBOX_MIN_TRADES and
        lock['profitFactor']>1 and lock['expectancyPct']>0 and stress['profitFactor']>=1 and stress['expectancyPct']>=0)
    one_min=_one_minute_status()
    return {'ok':True,'version':'0.17.16','researchOnly':True,'qualityGate':'GOOD_ONLY',
        'candidateTrades':len(cands),'selectedForLockbox':selected,
        'walkForward':{'method':'expanding-non-overlap-purged-v2','purgeDays':WF_PURGE_DAYS,
                       'minTrainTrades':WF_MIN_TRAIN_TRADES,'folds':len(results),
                       'positiveFolds':positive,'requiredPositiveFolds':required_positive,'results':results},
        'experiment':experiment,'lockbox':lock,'lockboxMinTrades':LOCKBOX_MIN_TRADES,'lockboxStress':stress,'oneMinuteExitValidation':one_min,
        'developmentHoldoutDays':len(development_holdout),
        'pass':research_pass,'researchReplayReady':bool(research_pass and one_min.get('ready')),'deploymentReady':False,
        'gate':'frozen future window + unchanged code/config/data + once-only final >=20 trades + 2x slippage/1bar-late',
        'deploymentGate':'Real execution remains unimplemented; research results cannot authorize deployment.',
        'liveRuleAutoMutation':False,'realOrderEnabled':False}
