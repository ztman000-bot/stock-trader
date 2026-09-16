"""Separate cash-constrained research account. Never reads/writes Paper state."""
from datetime import datetime
from math import floor, isfinite

MODEL_VERSION = 'research-account-1'


def evaluate_account(trades, *, initial_cash=10_000_000.0, allocation=.25, max_positions=2):
    if not isfinite(initial_cash) or initial_cash <= 0 or not 0 < allocation <= 1 or max_positions < 1:
        raise ValueError('invalid research account configuration')
    ordered = sorted(trades, key=lambda x: (x['entryAt'], str(x.get('code', ''))))
    events = []
    for key, trade in enumerate(ordered):
        entry_at, exit_at = map(datetime.fromisoformat, (trade['entryAt'], trade['exitAt']))
        if exit_at < entry_at:
            raise ValueError('exit precedes entry')
        for field in ('entryFill', 'entryUnitCost', 'exitUnitProceeds'):
            if not isfinite(float(trade[field])) or float(trade[field]) <= 0:
                raise ValueError('invalid research trade price')
        # Earlier closes release cash before new entries. Immediate exits must
        # follow their own entry, so use a separate event priority for that case.
        events.extend([(entry_at, 2, key, 'entry', trade),
                       (exit_at, 3 if exit_at == entry_at else 1, key, 'exit', trade)])
        for mark in trade.get('marks', []):
            at = datetime.fromisoformat(mark['at'])
            price = float(mark['price'])
            if not isfinite(price) or price <= 0:
                raise ValueError('invalid account mark')
            if entry_at < at < exit_at:
                events.append((at, 0, key, 'mark', price))
    events.sort(key=lambda e: e[:3])
    cash = peak = float(initial_cash)
    positions = {}
    pnl = []
    rejected = {'capacity': 0, 'cash': 0, 'sameInstrument': 0, 'protected': 0}
    curve = []
    mdd = 0.0

    def equity():
        return cash + sum(p['qty'] * p['mark'] for p in positions.values())

    def record_curve(at):
        nonlocal peak, mdd
        eq = equity()
        peak = max(peak, eq)
        mdd = min(mdd, (eq / peak - 1) * 100)
        curve.append({'at': at.isoformat(), 'equity': round(eq, 2), 'cash': round(cash, 2)})

    previous_at = None
    for at, _, key, kind, value in events:
        if at != previous_at:
            if previous_at is not None:
                record_curve(previous_at)
            previous_at = at
        if kind == 'entry':
            code = str(value.get('code', ''))
            if code == '068270':
                rejected['protected'] += 1
                continue
            if any(p['code'] == code for p in positions.values()):
                rejected['sameInstrument'] += 1
                continue
            if len(positions) >= max_positions:
                rejected['capacity'] += 1
                continue
            qty = floor(min(cash, max(0, equity()) * allocation) / value['entryUnitCost'])
            if qty < 1:
                rejected['cash'] += 1
                continue
            cost = qty * value['entryUnitCost']
            cash -= cost
            positions[key] = {'code': code, 'qty': qty, 'cost': cost, 'mark': value['entryFill']}
        elif key in positions:
            if kind == 'mark':
                positions[key]['mark'] = value
            else:
                p = positions.pop(key)
                proceeds = p['qty'] * value['exitUnitProceeds']
                cash += proceeds
                pnl.append(proceeds - p['cost'])
        else:
            continue
    if previous_at is not None:
        record_curve(previous_at)
    gains, losses = sum(max(x, 0) for x in pnl), -sum(min(x, 0) for x in pnl)
    return {'model': MODEL_VERSION, 'researchOnly': True, 'realOrderEnabled': False,
            'initialCash': initial_cash, 'finalEquity': round(equity(), 2),
            'netPnl': round(equity() - initial_cash, 2),
            'returnPct': round((equity() / initial_cash - 1) * 100, 4),
            'maxDrawdownPct': round(mdd, 4), 'filledTrades': len(pnl),
            'profitFactor': round(gains / losses, 4) if losses else (999 if gains else 0),
            'skipped': rejected, 'openPositions': len(positions), 'equityCurve': curve,
            'assumptions': {'allocation': allocation, 'maxPositions': max_positions,
                            'marking': '5m closes; intrabar drawdown is not observable',
                            'sizing': 'separate research configuration, not Control risk rules'}}
