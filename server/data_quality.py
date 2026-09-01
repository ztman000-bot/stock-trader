"""Market-data quality gates for research/backtests.
Read-only: grades 5m code/day data and exposes GOOD-only keys for research.
"""
import sqlite3
from datetime import datetime
from collector import DB_PATH,KST

EXPECTED_BUCKETS=set(range(540,926,5))

def _grade_day(arr):
    seen=set();dup=bad=0
    for row in arr:
        _,_,bucket,o,h,l,cl,v=row
        try:
            dt=datetime.fromisoformat(str(bucket)).astimezone(KST);hm=dt.hour*60+dt.minute
            if hm in seen:dup+=1
            seen.add(hm)
            if min(float(o),float(h),float(l),float(cl))<=0 or int(v)<0:bad+=1
        except Exception:
            bad+=1
    missing=len(EXPECTED_BUCKETS-seen);n=len(arr)
    grade='GOOD' if n>=76 and missing<=2 and dup==0 and bad==0 else 'PARTIAL' if n>=65 and missing<=13 and bad==0 else 'BAD'
    return {'bars':n,'missingBuckets':missing,'duplicates':dup,'badRows':bad,'grade':grade}

def quality_details(max_days=120):
    max_days=max(1,int(max_days))
    with sqlite3.connect(DB_PATH,timeout=10) as c:
        rows=c.execute("SELECT code,substr(bucket,1,10) d,bucket,open,high,low,close,volume FROM bars_5m ORDER BY code,bucket").fetchall()
    groups={}
    for row in rows:
        groups.setdefault((str(row[0]),str(row[1])),[]).append(row)
    days=sorted({d for _,d in groups})[-max_days:]
    keep=set(days);out=[]
    for (code,day),arr in groups.items():
        if day not in keep:continue
        out.append({'code':code,'date':day,**_grade_day(arr)})
    out.sort(key=lambda x:(x['date'],x['code']))
    return out

def quality_map(max_days=120):
    return {(x['code'],x['date']):x['grade'] for x in quality_details(max_days)}

def good_day_keys(max_days=120):
    return {k for k,v in quality_map(max_days).items() if v=='GOOD'}

def audit(max_days=120):
    out=quality_details(max_days)
    counts={g:sum(1 for x in out if x['grade']==g) for g in ('GOOD','PARTIAL','BAD')}
    return {
        'ok':True,'gate':'GOOD_ONLY_FOR_PROFITABILITY_RESEARCH','daysAudited':len(out),
        'counts':counts,'goodPct':round(100*counts['GOOD']/len(out),2) if out else 0,
        'badExamples':[x for x in out if x['grade']!='GOOD'][-20:]
    }
