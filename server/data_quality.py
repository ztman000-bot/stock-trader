"""Market-data quality gates for research/backtests.

Structural quality remains visible for diagnostics, while profitability/robust
research uses a stricter grade that also requires official NH 5-minute provenance.
This module never changes Control/Paper trading behavior.
"""
import sqlite3
from datetime import datetime
from collector import DB_PATH,KST

EXPECTED_BUCKETS=set(range(540,926,5))
OFFICIAL_SOURCE='nh_period_5m'
MIN_OFFICIAL_BARS=76


def _ensure_provenance(c):
    c.execute('''CREATE TABLE IF NOT EXISTS bar_5m_provenance(
      code TEXT NOT NULL,bucket TEXT NOT NULL,source TEXT NOT NULL,updated_at TEXT NOT NULL,
      PRIMARY KEY(code,bucket))''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_bar_5m_provenance_source ON bar_5m_provenance(source,code,bucket)')


def _grade_day(arr):
    seen=set();dup=bad=official=0
    for row in arr:
        _,_,bucket,o,h,l,cl,v,source=row
        try:
            dt=datetime.fromisoformat(str(bucket)).astimezone(KST);hm=dt.hour*60+dt.minute
            if hm in seen:dup+=1
            seen.add(hm)
            if min(float(o),float(h),float(l),float(cl))<=0 or int(v)<0:bad+=1
            if str(source or '')==OFFICIAL_SOURCE:official+=1
        except Exception:
            bad+=1
    missing=len(EXPECTED_BUCKETS-seen);n=len(arr)
    grade='GOOD' if n>=76 and missing<=2 and dup==0 and bad==0 else 'PARTIAL' if n>=65 and missing<=13 and bad==0 else 'BAD'
    official_ready=bool(grade=='GOOD' and official>=MIN_OFFICIAL_BARS)
    research_grade='GOOD' if official_ready else 'PARTIAL' if grade!='BAD' else 'BAD'
    return {
        'bars':n,'missingBuckets':missing,'duplicates':dup,'badRows':bad,'grade':grade,
        'officialBars':official,'unknownBars':max(0,n-official),
        'officialPct':round(100*official/n,2) if n else 0,
        'officialReady':official_ready,'researchGrade':research_grade,
    }


def quality_details(max_days=120):
    max_days=max(1,int(max_days))
    with sqlite3.connect(DB_PATH,timeout=10) as c:
        _ensure_provenance(c)
        rows=c.execute('''SELECT b.code,substr(b.bucket,1,10) d,b.bucket,b.open,b.high,b.low,b.close,b.volume,p.source
                          FROM bars_5m b LEFT JOIN bar_5m_provenance p
                          ON p.code=b.code AND p.bucket=b.bucket
                          ORDER BY b.code,b.bucket''').fetchall()
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
    """Structural bar-quality map retained for diagnostics/backward compatibility."""
    return {(x['code'],x['date']):x['grade'] for x in quality_details(max_days)}


def research_quality_map(max_days=120):
    """Profitability/Robust gate: structurally GOOD and >=76 official NH 5m bars."""
    return {(x['code'],x['date']):x['researchGrade'] for x in quality_details(max_days)}


def good_day_keys(max_days=120):
    return {k for k,v in quality_map(max_days).items() if v=='GOOD'}


def official_good_day_keys(max_days=120):
    return {k for k,v in research_quality_map(max_days).items() if v=='GOOD'}


def audit(max_days=120):
    out=quality_details(max_days)
    counts={g:sum(1 for x in out if x['grade']==g) for g in ('GOOD','PARTIAL','BAD')}
    research_counts={g:sum(1 for x in out if x['researchGrade']==g) for g in ('GOOD','PARTIAL','BAD')}
    official_good=research_counts['GOOD']
    return {
        'ok':True,'gate':'OFFICIAL_NH_GOOD_ONLY_FOR_PROFITABILITY_RESEARCH','daysAudited':len(out),
        'counts':counts,'researchCounts':research_counts,
        'goodPct':round(100*counts['GOOD']/len(out),2) if out else 0,
        'officialGoodPct':round(100*official_good/len(out),2) if out else 0,
        'minOfficialBars':MIN_OFFICIAL_BARS,'officialSource':OFFICIAL_SOURCE,
        'badExamples':[x for x in out if x['grade']!='GOOD'][-20:],
        'unverifiedExamples':[x for x in out if x['grade']=='GOOD' and not x['officialReady']][-20:]
    }
