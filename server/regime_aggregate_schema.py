"""Shared, standard-library validation for compact aggregate references."""
from datetime import date
from math import isfinite

SCHEMA_VERSION = 2
EXTRA_FIELDS = ('schema_version', 'universe_count', 'return_observation_count',
                'missing_return_count', 'equal_weight_change_pct', 'active_market_cap_total')
NUMERIC_FIELDS = ('active_count', 'advancers', 'decliners', 'unchanged', 'advance_ratio',
                  'decline_ratio', 'turnover_amount', 'market_cap_total', 'top10_cap_share',
                  'cap_weighted_change_pct', 'median_change_pct', 'breadth_5d',
                  'weighted_return_20d', 'volatility_20d', 'turnover_ratio_20d', 'regime_score',
                  *EXTRA_FIELDS)
COUNTS = ('active_count', 'advancers', 'decliners', 'unchanged', 'universe_count',
          'return_observation_count', 'missing_return_count')
RATIOS = ('advance_ratio', 'decline_ratio', 'top10_cap_share', 'breadth_5d')
REGIMES = {'WARMUP': 0, 'STRESS': -2, 'RISK_OFF': -1, 'NEUTRAL': 0, 'RISK_ON': 1, 'RISK_ON_STRONG': 2}


def validate_row(row):
    raw_date = str(row.get('trade_date', ''))
    if date.fromisoformat(raw_date).isoformat() != raw_date:
        raise ValueError('trade_date must be an ISO date')
    if row.get('market') not in ('KOSPI', 'KOSDAQ') or row.get('source') != 'GITHUB_FINANCEDATA_MARCAP_AGG':
        raise ValueError('invalid aggregate provenance/market')
    if not row.get('source_ref') or row.get('regime') not in REGIMES:
        raise ValueError('invalid aggregate source reference/regime')
    if None in row:
        raise ValueError('CSV row has unexpected extra values')
    values = {}
    for field in NUMERIC_FIELDS:
        raw = row.get(field)
        if raw is None or str(raw).strip() == '':
            continue
        value = float(raw)
        if not isfinite(value):
            raise ValueError(f'non-finite {field}')
        if field in COUNTS and (value < 0 or not value.is_integer()):
            raise ValueError(f'invalid count {field}')
        if field in RATIOS and not 0 <= value <= 1:
            raise ValueError(f'invalid ratio {field}')
        if field in ('turnover_amount', 'market_cap_total', 'active_market_cap_total', 'volatility_20d', 'turnover_ratio_20d') and value < 0:
            raise ValueError(f'negative {field}')
        values[field] = value
    version = values.get('schema_version', 1)
    if version not in (1, 2):
        raise ValueError('unsupported aggregate schema')
    if values.get('regime_score') != REGIMES[row['regime']]:
        raise ValueError('regime score/label mismatch')
    if not all(field in values for field in ('active_count', 'advancers', 'decliners', 'unchanged')):
        raise ValueError('missing breadth counts')
    observed = sum(values[k] for k in ('advancers', 'decliners', 'unchanged'))
    if observed > values['active_count']:
        raise ValueError('breadth counts exceed active universe')
    if version == 2:
        if not all(field in values for field in ('universe_count', 'return_observation_count', 'missing_return_count', 'active_market_cap_total')):
            raise ValueError('missing v2 counts/capitalization')
        if observed != values['return_observation_count'] or observed + values['missing_return_count'] != values['active_count']:
            raise ValueError('missing-return accounting mismatch')
        if values['active_count'] > values['universe_count'] or values['active_market_cap_total'] > values.get('market_cap_total', -1) + .01:
            raise ValueError('active universe exceeds full universe')
    denominator = observed if version == 2 else values['active_count']
    if denominator:
        for ratio, count in (('advance_ratio', 'advancers'), ('decline_ratio', 'decliners')):
            if ratio not in values or abs(values[ratio] - values[count] / denominator) > .000002:
                raise ValueError('breadth ratio/count mismatch')
    return int(version)


def validate_rows(rows):
    if not rows:
        raise ValueError('summary contains no rows')
    seen = set()
    versions = set()
    for row in rows:
        versions.add(validate_row(row))
        key = (row['trade_date'], row['market'])
        if key in seen:
            raise ValueError('duplicate date/market aggregate')
        seen.add(key)
    if len(versions) != 1:
        raise ValueError('mixed aggregate definitions; rebuild the full reference')
    return versions.pop()
