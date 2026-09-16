"""Pure, long-only bar simulation for research; no Control/Paper dependencies."""
from datetime import datetime, timedelta
from math import isfinite
from statistics import mean

MODEL_VERSION = 'research-fills-2'


def atr_before_entry(rows, index, period=14):
    """Use only completed bars strictly before the entry bar."""
    if index < 2 or index >= len(rows):
        return None
    ranges = []
    for j in range(max(1, index - period), index):
        previous = float(rows[j - 1]['close'])
        high, low = float(rows[j]['high']), float(rows[j]['low'])
        ranges.append(max(high - low, abs(high - previous), abs(low - previous)))
    price = float(rows[index]['open'])
    return mean(ranges) / price if ranges and price > 0 else None


def simulate_trade(rows, index, config, *, commission, sell_tax, slippage, late_bars=0):
    """Conservative two-path OHLC estimate, including gaps through active stops.

    Intrabar exit times are placed at bar end for account scheduling; they are
    estimates, not observed executions. The lower net outcome of OHLC/OLHC wins.
    """
    if not 0 <= index < len(rows):
        return None
    day = datetime.fromisoformat(rows[index]['bucket']).date()
    index += max(0, int(late_bars))
    if index >= len(rows) or datetime.fromisoformat(rows[index]['bucket']).date() != day:
        return None
    if any(not isfinite(float(x)) or float(x) < 0 for x in (commission, sell_tax, slippage)):
        raise ValueError('invalid research costs')
    entry = float(rows[index]['open']) * (1 + slippage)
    if not isfinite(entry) or entry <= 0:
        raise ValueError('invalid entry price')
    stop = float(config['stop'])
    if config.get('atr'):
        atr = atr_before_entry(rows, index)
        if atr is not None:
            stop = max(.006, min(.012, atr * 1.25))

    def levels(peak):
        result = [('STOP_LOSS', entry * (1 - stop))]
        if peak >= entry * (1 + config['be_act']):
            result.append(('BREAKEVEN', entry * (1 + config['be_lock'])))
        trail = config['trail']
        if config.get('dynamic_trail'):
            mfe = peak / entry - 1
            trail = max(trail, .012 if mfe >= .030 else .010 if mfe >= .020 else .0085 if mfe >= .015 else trail)
        if peak >= entry * (1 + config['trail_act']):
            result.append(('TRAILING_STOP', peak * (1 - trail)))
        return result

    def path_result(path):
        peak = entry
        marks = []
        last = None
        for j in range(index, min(len(rows), index + 78)):
            row = rows[j]
            at = datetime.fromisoformat(row['bucket'])
            if at.date() != day:
                break
            o, h, l, c = (float(row[k]) for k in ('open', 'high', 'low', 'close'))
            if not all(isfinite(v) and v > 0 for v in (o, h, l, c)) or not l <= min(o, c) <= max(o, c) <= h:
                raise ValueError('invalid research OHLC')
            end = at + timedelta(minutes=5)
            last = (c, end, 'END_OF_COVERAGE')
            crossed = [(reason, level) for reason, level in levels(peak) if o <= level]
            if crossed:
                return finish(o, at, max(crossed, key=lambda x: x[1])[0], marks, path)
            peak = max(peak, o)
            previous = o
            for price in ((h, l, c) if path == 'OHLC' else (l, h, c)):
                if price < previous:
                    crossed = [(reason, level) for reason, level in levels(peak) if price <= level <= previous]
                    if crossed:
                        reason, level = max(crossed, key=lambda x: x[1])
                        return finish(level, end, reason, marks, path)
                peak = max(peak, price)
                previous = price
            marks.append({'at': end.isoformat(), 'price': c})
            if (config.get('failure_bars') and j - index + 1 >= config['failure_bars']
                    and peak / entry - 1 < config['min_mfe']
                    and c / entry - 1 <= config.get('failure_max_return', 999)):
                return finish(c, end, 'TIME_FAILURE', marks, path)
            if at.hour * 60 + at.minute >= 915:
                return finish(c, end, 'EOD_EXIT', marks, path)
        return finish(*last, marks, path) if last else None

    def finish(price, at, reason, marks, path):
        exit_fill = price * (1 - slippage)
        entry_cost = entry * (1 + commission)
        proceeds = exit_fill * (1 - commission - sell_tax)
        return {'entryAt': rows[index]['bucket'], 'exitAt': at.isoformat(),
                'entryFill': entry, 'exitFill': exit_fill, 'entryUnitCost': entry_cost,
                'exitUnitProceeds': proceeds, 'netPct': (proceeds - entry_cost) / entry * 100,
                'stopPct': stop, 'reason': reason, 'marks': marks, 'path': path,
                'fillModel': MODEL_VERSION, 'timeModel': 'intrabar-exit-at-bar-end'}

    results = [path_result(path) for path in ('OHLC', 'OLHC')]
    return min((r for r in results if r is not None), key=lambda r: r['netPct'], default=None)
