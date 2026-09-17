"""Independent session expectations for operational monitoring only.

Never imported by Control/Paper or used to change collection/trading schedules.
An expired or unavailable reference is UNKNOWN, never inferred from missing data.
"""
import json
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo('Asia/Seoul')
CALENDAR_PATH = Path(__file__).resolve().parent / 'data/krx_sessions.json'


@lru_cache(maxsize=1)
def load_calendar(path=CALENDAR_PATH):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if (data['schemaVersion'] != 1 or data['market'] != 'XKRX'
            or data['timezone'] != 'Asia/Seoul' or not data.get('provenance')):
        raise ValueError('unsupported session reference')
    first, last = date.fromisoformat(data['validFrom']), date.fromisoformat(data['validThrough'])
    if first > last:
        raise ValueError('invalid calendar range')
    sessions = {}
    for day, start, end in data['sessions']:
        d = date.fromisoformat(day)
        opened = datetime.fromisoformat(f'{day}T{start}').replace(tzinfo=KST)
        closed = datetime.fromisoformat(f'{day}T{end}').replace(tzinfo=KST)
        seconds = (closed - opened).total_seconds()
        if (day in sessions or not first <= d <= last or d.weekday() >= 5
                or seconds <= 0 or seconds % 300 or opened.minute % 5 or closed.minute % 5):
            raise ValueError('invalid session')
        sessions[day] = (opened, closed)
    if not sessions:
        raise ValueError('empty calendar')
    return data, sessions


def calendar_window(now=None, limit=5, path=CALENDAR_PATH):
    now = (now or datetime.now(KST)).astimezone(KST)
    try:
        data, sessions = load_calendar(path)
        day = now.date().isoformat()
        dates = sorted(d for d in sessions if d <= day)[-max(1, int(limit)):]
        ready = data['validFrom'] <= day <= data['validThrough'] and len(dates) >= limit
        return {'ok': ready, 'state': 'REFERENCE_AVAILABLE' if ready else 'CALENDAR_UNKNOWN',
                'validFrom': data['validFrom'], 'validThrough': data['validThrough'],
                'source': data['provenance']['generator'], 'officialFeed': False,
                'days': dates if ready else [], 'researchOnly': True}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return {'ok': False, 'state': 'CALENDAR_UNKNOWN', 'days': [],
                'error': type(exc).__name__, 'researchOnly': True}


def session_coverage(day, timestamps, now=None, min_pct=95.0, path=CALENDAR_PATH):
    now = (now or datetime.now(KST)).astimezone(KST)
    _, sessions = load_calendar(path)
    opened, closed = sessions[day]
    expected = max(0, int((min(now, closed) - opened).total_seconds()) // 300)
    full = int((closed - opened).total_seconds()) // 300
    slots = set()
    ignored = 0
    for value in timestamps:
        try:
            at = datetime.fromisoformat(str(value))
            at = at.replace(tzinfo=KST) if at.tzinfo is None else at.astimezone(KST)
            slot = int((at - opened).total_seconds() // 300)
            if 0 <= slot < expected:
                slots.add(slot)
            else:
                ignored += 1
        except (ValueError, TypeError):
            ignored += 1
    actual = len(slots)
    pct = round(100 * actual / expected, 2) if expected else None
    return {'date': day, 'expectedSnapshots': expected, 'fullDaySnapshots': full,
            'actualSnapshots': actual, 'missingSnapshots': expected - actual,
            'ignoredTimestamps': ignored, 'coveragePct': pct,
            'wholeDayMissing': now >= closed and actual == 0,
            'sessionOpen': opened.isoformat(), 'sessionClose': closed.isoformat(),
            'state': 'PENDING' if not expected else ('COMPLETE' if pct >= min_pct else 'INCOMPLETE_DAY')}
