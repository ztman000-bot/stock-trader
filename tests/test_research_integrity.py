"""Behavioral regressions; temporary data only, no live services or Paper writes."""
import ast
import copy
import csv
import io
import json
import math
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import mean
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))
from research_fills import atr_before_entry, simulate_trade
from research_portfolio import evaluate_account
import research_experiments as experiments
from execution_simulation import SimulationJournal, run_simulations
import marcap_regime_builder as builder
import github_regime_reference as reference


CONFIG = {'stop': .01, 'trail_act': .015, 'trail': .008, 'be_act': .008,
          'be_lock': .0035, 'atr': False, 'failure_bars': 0, 'dynamic_trail': False}
COSTS = {'commission': .0001, 'sell_tax': .0015, 'slippage': .0005}


def bars(count=20):
    start = datetime.fromisoformat('2026-09-01T09:00:00+09:00')
    return [{'bucket': (start + timedelta(minutes=i * 5)).isoformat(),
             'open': 100, 'high': 100.1, 'low': 99.9, 'close': 100, 'volume': 1000}
            for i in range(count)]


def isolated_functions(filename, names, namespace):
    tree = ast.parse((ROOT / 'server' / filename).read_text())
    functions = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    exec(compile(ast.Module(body=functions, type_ignores=[]), filename, 'exec'), namespace)
    return namespace


class FillIntegrityTests(unittest.TestCase):
    def test_frozen_date_scope_keeps_official_good_gate_and_protected_exclusion(self):
        data = bars(2) + [{**r,'bucket':'2026-09-02'+r['bucket'][10:]} for r in bars(2)]
        signals = Mock(return_value=(data,[1,3]))
        quality = Mock(return_value={('005930','2026-09-01'):'GOOD',('005930','2026-09-02'):'PARTIAL'})
        ns = {'_signals':signals,'quality_map':quality,'_dt':datetime.fromisoformat,
              '_build_regime':lambda codes:{},'_feature':lambda rows,i:{'time':'09:00'},
              '_early_surge_score':lambda rows,i:None,'historical_score':lambda rows,i:None}
        fn = isolated_functions('profitability_lab.py',{'_candidates'},ns)['_candidates']
        candidates = fn(codes=['068270','069500','005930'],quality_days=1_000_000)
        self.assertEqual(len(candidates),1)
        self.assertEqual(str(candidates[0]['date']),'2026-09-01')
        signals.assert_called_once_with('005930','cross_trend_v2')
        quality.assert_called_once_with(1_000_000)
        self.assertEqual(fn(codes=['005930'],min_date='2026-09-02'),[])

    def test_entry_bar_future_high_low_cannot_change_atr_stop(self):
        data = bars()
        first = simulate_trade(data, 15, {**CONFIG, 'atr': True}, **COSTS)
        data[15].update(high=115, low=95)
        changed = simulate_trade(data, 15, {**CONFIG, 'atr': True}, **COSTS)
        self.assertAlmostEqual(first['stopPct'], .006)
        self.assertEqual(first['stopPct'], changed['stopPct'])
        self.assertAlmostEqual(atr_before_entry(data, 15), .002)

    def test_atr_includes_last_completed_bar(self):
        data = bars()
        data[14].update(high=110, low=90)
        self.assertGreater(atr_before_entry(data, 15), .012)

    def test_gap_below_stop_uses_observed_open_and_costs(self):
        data = bars(3)
        data[1].update(open=97, high=98, low=96, close=97)
        result = simulate_trade(data, 0, CONFIG, **COSTS)
        self.assertAlmostEqual(result['exitFill'], 97 * (1 - COSTS['slippage']))
        self.assertEqual(result['exitAt'], data[1]['bucket'])
        self.assertLess(result['netPct'], -3)

    def test_intrabar_stop_crossing_still_uses_threshold(self):
        data = bars(1)
        data[0].update(low=98, close=99)
        result = simulate_trade(data, 0, CONFIG, commission=0, sell_tax=0, slippage=0)
        self.assertEqual(result['exitFill'], 99)

    def test_costs_and_late_entry_have_explicit_cash_values(self):
        data = bars(3)
        plain = simulate_trade(data, 0, CONFIG, commission=0, sell_tax=0, slippage=0)
        priced = simulate_trade(data, 0, CONFIG, **COSTS, late_bars=1)
        self.assertLess(priced['netPct'], plain['netPct'])
        self.assertEqual(priced['entryAt'], data[1]['bucket'])
        self.assertAlmostEqual(priced['entryUnitCost'], priced['entryFill'] * (1 + COSTS['commission']))
        self.assertAlmostEqual(priced['exitUnitProceeds'], priced['exitFill'] * (1 - COSTS['commission'] - COSTS['sell_tax']))

    def test_late_entry_cannot_cross_session(self):
        data = bars(2)
        data[1]['bucket'] = '2026-09-02T09:00:00+09:00'
        self.assertIsNone(simulate_trade(data, 0, CONFIG, **COSTS, late_bars=1))

    def test_invalid_ohlc_is_not_silently_simulated(self):
        data = bars(1)
        data[0]['high'] = 90
        with self.assertRaises(ValueError):
            simulate_trade(data, 0, CONFIG, **COSTS)

    def test_one_minute_gap_preserves_reason_but_not_unavailable_trigger_fill(self):
        ns = {'STOP_PCT': .01, 'TRAIL_ACTIVATE_PCT': .015, 'TRAIL_PCT': .008,
              'BREAKEVEN_ACTIVATE_PCT': .008, 'BREAKEVEN_BUFFER_PCT': .0035,
              '_dt': datetime.fromisoformat}
        isolated_functions('one_minute_exit_replay.py', {'_gap_exit', '_levels', '_descend_exit', '_simulate'}, ns)
        row = {**bars(1)[0], 'open': 97, 'high': 98, 'low': 96, 'close': 97}
        for path in ('OHLC', 'OLHC'):
            result = ns['_simulate']([row], 100, path)
            self.assertEqual((result['reason'], result['marketExit']), ('STOP_LOSS', 97))
        self.assertEqual(ns['_gap_exit'](100, 103, 100), ('TRAILING_STOP', 100))
        self.assertEqual(ns['_gap_exit'](100, 101, 100), ('COST_COVER_PROTECT', 100))


class AccountIntegrityTests(unittest.TestCase):
    def trade(self, code='005930', **changes):
        return {'code': code, 'entryAt': '2026-09-01T10:00:00+09:00',
                'exitAt': '2026-09-01T10:15:00+09:00', 'entryFill': 100,
                'entryUnitCost': 100, 'exitUnitProceeds': 100, 'marks': [], **changes}

    def test_mdd_includes_unrealized_loss_and_recovery(self):
        trade = self.trade(marks=[{'at': '2026-09-01T10:05:00+09:00', 'price': 80}])
        result = evaluate_account([trade], initial_cash=1000, allocation=.5)
        self.assertEqual(result['netPnl'], 0)
        self.assertAlmostEqual(result['maxDrawdownPct'], -10)

    def test_cash_and_concurrent_position_limits(self):
        trades = [self.trade(code) for code in ('005930', '000660', '035420')]
        result = evaluate_account(trades, initial_cash=1000, allocation=.6, max_positions=2)
        self.assertEqual(result['filledTrades'], 2)
        self.assertEqual(result['skipped']['capacity'], 1)
        self.assertTrue(all(p['cash'] >= 0 for p in result['equityCurve']))
        self.assertEqual(result['finalEquity'], 1000)

    def test_commission_and_integer_quantity_affect_equity(self):
        trade = self.trade(entryUnitCost=101, exitUnitProceeds=99)
        result = evaluate_account([trade], initial_cash=1000, allocation=1)
        self.assertEqual(result['netPnl'], -18)
        self.assertEqual(result['finalEquity'], 982)

    def test_same_timestamp_marks_are_one_account_observation(self):
        at = '2026-09-01T10:05:00+09:00'
        trades = [self.trade('000660', marks=[{'at': at, 'price': 110}]),
                  self.trade('005930', marks=[{'at': at, 'price': 90}])]
        result = evaluate_account(trades, initial_cash=1000, allocation=.5)
        self.assertEqual(result['maxDrawdownPct'], 0)

    def test_protected_code_and_duplicate_instrument_are_skipped(self):
        result = evaluate_account([self.trade('068270'), self.trade(), self.trade()])
        self.assertEqual(result['skipped']['protected'], 1)
        self.assertEqual(result['skipped']['sameInstrument'], 1)


class FrozenExperimentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'experiments.db'
        self.data = [{'code': '005930', 'date': date(2026, 9, 1), 'i': 0, 'rows': bars(2)}]
        self.settings = {'cost': .001}
        self.record = experiments.freeze(self.data, {'candidate': 'fixed'}, self.settings,
                                          path=self.path, today=date(2026, 9, 2), source_hash='code-a')

    def tearDown(self):
        self.temp.cleanup()

    def evaluate(self, evaluator, **kwargs):
        return experiments.evaluate_once(experiments.active(self.path), kwargs.pop('data', self.data),
                                         kwargs.pop('settings', self.settings), evaluator,
                                         path=self.path, today=kwargs.pop('today', date(2026, 10, 20)),
                                         source_hash=kwargs.pop('source_hash', 'code-a'))

    def test_future_period_and_candidate_remain_fixed_when_data_grows(self):
        changed = self.data + [{'code': '005930', 'date': date(2026, 9, 10), 'i': 0, 'rows': []}]
        same = experiments.freeze(changed, {'candidate': 'different'}, {}, path=self.path,
                                  today=date(2026, 9, 20), source_hash='changed')
        self.assertEqual(same['id'], self.record['id'])
        self.assertEqual(same['manifest']['selection'], {'candidate': 'fixed'})
        self.assertEqual(same['manifest']['lockboxStart'], '2026-09-03')
        self.assertEqual(same['manifest']['lockboxEnd'], '2026-10-14')
        self.assertEqual(experiments.development_candidates(changed, self.path), self.data)

    def test_final_is_not_evaluated_before_window_is_closed(self):
        evaluator = Mock()
        result = self.evaluate(evaluator, today=date(2026, 10, 14))
        self.assertEqual(result['status'], 'WAITING_FOR_CLOSED_WINDOW')
        evaluator.assert_not_called()

    def test_final_result_evaluated_once_and_cached(self):
        evaluator = Mock(return_value={'lockbox': {'trades': 22}})
        first = self.evaluate(evaluator)
        second = self.evaluate(evaluator)
        self.assertTrue(first['finalEvidence'])
        self.assertTrue(second['cached'])
        self.assertEqual(first['result'], second['result'])
        evaluator.assert_called_once()

    def test_later_dates_do_not_move_or_reopen_completed_final_window(self):
        evaluator = Mock(return_value={'trades': 25})
        self.evaluate(evaluator)
        changed = self.data + [{'code':'005930','date':date(2026,11,1),'i':0,'rows':[]}]
        result = self.evaluate(evaluator, data=changed, today=date(2026,11,2))
        self.assertTrue(result['cached'])
        evaluator.assert_called_once()

    def test_code_configuration_and_historical_revisions_block_evaluation(self):
        changed = copy.deepcopy(self.data)
        changed[0]['rows'][0]['close'] = 100.01
        for kwargs in ({'source_hash': 'code-b'}, {'settings': {'cost': .002}}, {'data': changed}):
            with self.subTest(kwargs=kwargs):
                evaluator = Mock()
                result = self.evaluate(evaluator, **kwargs)
                self.assertEqual(result['status'], 'INTEGRITY_BLOCKED')
                evaluator.assert_not_called()

    def test_failed_final_evaluation_cannot_automatically_retry(self):
        with self.assertRaises(RuntimeError):
            self.evaluate(Mock(side_effect=RuntimeError('interrupted')))
        evaluator = Mock()
        self.assertEqual(self.evaluate(evaluator)['status'], 'CONSUMED_OR_RUNNING')
        evaluator.assert_not_called()

    def test_completed_final_data_revision_invalidates_cached_claim(self):
        self.evaluate(Mock(return_value={'trades': 30}))
        changed = self.data + [{'code': '005930', 'date': date(2026, 9, 10), 'i': 0, 'rows': []}]
        result = self.evaluate(Mock(), data=changed)
        self.assertEqual(result['status'], 'INTEGRITY_BLOCKED')
        self.assertFalse(result['finalEvidence'])

    def test_event_history_and_manifest_cannot_be_overwritten(self):
        with sqlite3.connect(self.path) as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE experiments SET manifest='{}'")
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute('DELETE FROM experiment_events')
        experiments.retire(self.record['id'], 'explicit new future experiment', self.path)
        self.assertIsNone(experiments.active(self.path))
        with sqlite3.connect(self.path) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM experiments').fetchone()[0], 1)

    def test_robust_pipeline_freezes_then_evaluates_future_once_without_live_promotion(self):
        # Exercise the actual orchestration and fill/account bodies, isolating only
        # the broker-backed candidate loader and coverage call.
        future_path = Path(self.temp.name) / 'pipeline.db'
        f = {'id':'test', 'name':'test', 'rvol':0, 'ema':-999, 'vwap':999, 'cutoff':9999, 'penalty':0}
        cfg = {**CONFIG, 'id':'fixed', 'name':'fixed', 'penalty':0, 'trail_act':999, 'be_act':999}
        all_mode = {'id':'all', 'name':'all', 'min':None, 'penalty':0}
        entry_mode = {'id':'signal_open', 'name':'signal_open', 'penalty':0}
        def candidate(day):
            rows = bars(3)
            for row in rows:
                row['bucket'] = str(day) + row['bucket'][10:]
            rows[-1].update(high=102, close=102)
            return {'code':'005930', 'date':day, 'i':0, 'rows':rows,
                    'feat':{'time':'09:00','rvol':2,'emaSpreadPct':1,'vwapDistPct':0},
                    'regime':'NEUTRAL','surgeScore':None,'sipScore':None}
        data = [candidate(date(2026,9,1)-timedelta(days=i)) for i in reversed(range(60))]
        ns = {'ceil':math.ceil,'datetime':datetime,'ZoneInfo':__import__('zoneinfo').ZoneInfo,
              'experiments':experiments,'mean':mean,'_dt':datetime.fromisoformat,
              'simulate_trade':simulate_trade,'evaluate_account':evaluate_account,
              'COMMISSION':.0001,'SELL_TAX':.0015,'BASE_SLIPPAGE':.0005,
              'FILTERS':[f],'EXIT_CONFIGS':[cfg],'REGIME_MODES':[all_mode],
              'SURGE_MODES':[all_mode],'ENTRY_MODES':[entry_mode],'PLAY_MODES':[all_mode],
              'WF_FOLDS':4,'WF_PURGE_DAYS':1,'WF_MIN_TRAIN_TRADES':12,'LOCKBOX_MIN_TRADES':20,
              '_candidates':lambda n,**kwargs:data,'available_codes':lambda:[{'code':'005930'}],
              '_one_minute_status':lambda:{'ready':True}}
        isolated_functions('profitability_lab.py', {'_metrics','_entry_filter','_regime_pass','_surge_pass',
                           '_play_pass','_resolve_entry_index','_trade','_eval'}, ns)
        isolated_functions('robust_validation.py', {'_date_slices','run_robust_validation'}, ns)
        fn = ns['run_robust_validation']
        first = fn(registry_path=future_path,today=date(2026,9,2))
        self.assertFalse(first['pass'])
        self.assertEqual(first['lockbox']['trades'],0)
        self.assertEqual(first['experiment']['status'],'WAITING_FOR_CLOSED_WINDOW')
        data.extend(candidate(date(2026,9,3)+timedelta(days=i)) for i in range(30))
        final = fn(registry_path=future_path,today=date(2026,10,15))
        self.assertTrue(final['pass'])
        self.assertTrue(final['researchReplayReady'])
        self.assertFalse(final['deploymentReady'])
        self.assertFalse(final['realOrderEnabled'])
        self.assertEqual(final['lockbox']['trades'],30)
        self.assertEqual(final['lockbox']['account']['filledTrades'],30)
        cached = fn(registry_path=future_path,today=date(2026,10,16))
        self.assertTrue(cached['experiment']['cached'])
        self.assertEqual(cached['lockbox'],final['lockbox'])


class ExecutionSimulationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'simulation.db'
        self.journal = SimulationJournal(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def intent(self, **changes):
        return self.journal.intent(**{'code': '005930', 'side': 'BUY', 'qty': 10,
                                     'trading_date': '2026-09-01', 'signal_bucket': '2026-09-01T10:00:00+09:00', **changes})

    def test_140_recovery_trials_remain_offline_and_not_live_ready(self):
        result = run_simulations()
        self.assertTrue(result['ok'], result['failures'])
        self.assertEqual(result['passed'], 140)
        self.assertFalse(result['nhBrokerValidated'])
        self.assertFalse(result['realOrderEnabled'])
        self.assertFalse(result['microLiveReady'])

    def test_persistent_intent_duplicate_conflict_and_fill_bounds(self):
        key = self.intent()
        self.journal.record(key, 'submit', 'SUBMITTED')
        self.assertEqual(self.intent(), key)
        with self.assertRaises(ValueError): self.intent(qty=20)
        self.journal.record(key, 'partial', 'PARTIALLY_FILLED', 4)
        self.assertFalse(self.journal.record(key, 'partial', 'PARTIALLY_FILLED', 4))
        with self.assertRaises(ValueError): self.journal.record(key, 'partial', 'PARTIALLY_FILLED', 5)
        with self.assertRaises(ValueError): self.journal.record(key, 'overfill', 'FILLED', 11)
        self.assertEqual(SimulationJournal(self.path).get(key)['filledQty'], 4)

    def test_unknown_order_missing_ack_and_protected_code_fail_closed(self):
        with self.assertRaises(ValueError): self.intent(code='068270')
        key = self.intent()
        self.journal.record(key, 'submit', 'SUBMITTED')
        result = self.journal.recover([])
        self.assertTrue(result['blocked'])
        self.assertEqual(result['resubmissions'], 0)

    def test_unrelated_database_and_event_mutation_are_rejected(self):
        other = Path(self.temp.name) / 'unrelated.db'
        with sqlite3.connect(other) as conn:
            conn.execute('CREATE TABLE existing_data(value INTEGER)')
        with self.assertRaises(ValueError): SimulationJournal(other)
        self.intent()
        with sqlite3.connect(self.path) as conn:
            with self.assertRaises(sqlite3.IntegrityError): conn.execute('DELETE FROM simulation_events')


class AggregateIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'reference.db'

    def tearDown(self):
        self.temp.cleanup()

    def sample(self):
        rows = [dict(Volume=100, Amount=1000, Marcap=100, ChangesRatio=r) for r in (1, 1, 10)]
        rows += [dict(Volume=0, Amount=0, Marcap=700, ChangesRatio=0),
                 dict(Volume=10, Amount=100, Marcap=100, ChangesRatio=math.nan)]
        return builder.aggregate_market(rows, '2026-09-01', 'KOSPI', 'FinanceData/marcap@test:data/marcap-2026.parquet')

    def csv(self, rows):
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=builder.SUMMARY_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
        return output.getvalue()

    def test_full_capitalization_equal_weight_and_missing_returns(self):
        row = self.sample()
        self.assertEqual(row['market_cap_total'], 1100)
        self.assertEqual(row['active_market_cap_total'], 400)
        self.assertEqual(row['equal_weight_change_pct'], 4)
        self.assertEqual(row['median_change_pct'], 1)
        self.assertEqual(row['missing_return_count'], 1)
        self.assertEqual(row['unchanged'], 0)
        self.assertEqual(row['advance_ratio'], 1)

    def test_schema_v2_roundtrip_to_separate_phone_database(self):
        rows = builder.add_rolling_regime([self.sample()])
        result = reference.import_summary_text(self.csv(rows), self.path)
        self.assertEqual(result['schemaVersion'], 2)
        current = reference.collector_status(self.path)['latest']['KOSPI']
        self.assertEqual(current['equalWeightChangePct'], 4)
        self.assertEqual(current['schemaVersion'], 2)
        self.assertIsNone(current['weightedReturn20d'])

    def test_concentration_includes_suspended_large_issue(self):
        rows = [dict(Volume=100, Amount=100, Marcap=1000, ChangesRatio=0) for _ in range(11)]
        rows.append(dict(Volume=0, Amount=0, Marcap=9000, ChangesRatio=0))
        result = builder.aggregate_market(rows,'2026-09-01','KOSPI','test')
        self.assertEqual(result['market_cap_total'],20000)
        self.assertEqual(result['top10_cap_share'],.9)

    def test_complete_snapshot_removes_absent_rows_and_blocks_schema_downgrade(self):
        row = builder.add_rolling_regime([self.sample()])[0]
        reference.import_summary_text(self.csv([row,{**row,'market':'KOSDAQ'}]),self.path)
        reference.import_summary_text(self.csv([row]),self.path)
        self.assertEqual(reference.collector_status(self.path)['rows'],1)
        legacy = {k:v for k,v in row.items() if k not in builder.EXTRA_FIELDS}
        legacy.update(unchanged=1,advance_ratio=.75)
        with self.assertRaises(ValueError):
            reference.import_summary_text(self.csv([legacy]),self.path)
        self.assertEqual(reference.collector_status(self.path)['latest']['KOSPI']['schemaVersion'],2)

    def test_schema_change_forces_full_rebuild_even_in_recent_mode(self):
        output = Path(self.temp.name)/'summary.csv'
        meta = Path(self.temp.name)/'meta.json'
        row = builder.add_rolling_regime([self.sample()])[0]
        legacy = {k:v for k,v in row.items() if k not in builder.EXTRA_FIELDS}
        output.write_text(self.csv([legacy]))
        def fake_aggregate(path,source_ref):
            year = int(path.stem.rsplit('-',1)[1])
            return [{**self.sample(),'trade_date':f'{year}-09-01','source_ref':source_ref}]
        with patch.object(builder,'_download'), patch.object(builder,'aggregate_parquet',side_effect=fake_aggregate):
            result = builder.build(output,meta,'test-sha',mode='recent')
        self.assertEqual(min(result['years']),1995)
        self.assertGreater(len(result['years']),2)
        self.assertEqual(result['schemaVersion'],2)
        self.assertEqual(json.loads(meta.read_text())['schemaVersion'],2)

    def test_invalid_summary_is_atomic_and_does_not_replace_last_good_metadata(self):
        rows = builder.add_rolling_regime([self.sample()])
        reference.import_summary_text(self.csv(rows), self.path, etag='good')
        for change in ({'trade_date': '2026-02-30'}, {'advance_ratio': 2},
                       {'cap_weighted_change_pct': 'NaN'}, {'unchanged': 9}):
            with self.subTest(change=change):
                with self.assertRaises(ValueError):
                    reference.import_summary_text(self.csv([rows[0], {**rows[0], **change}]), self.path, etag='bad')
                self.assertEqual(reference._meta('etag', self.path), 'good')
                self.assertEqual(reference.collector_status(self.path)['rows'], 1)

    def test_duplicate_summary_rows_rejected(self):
        row = builder.add_rolling_regime([self.sample()])[0]
        with self.assertRaises(ValueError):
            reference.import_summary_text(self.csv([row, row]), self.path)

    def test_missing_daily_return_resets_20_day_window(self):
        rows = [{**self.sample(), 'trade_date': (date(2026, 8, 1) + timedelta(days=i)).isoformat()} for i in range(22)]
        rows[20]['cap_weighted_change_pct'] = None
        result = builder.add_rolling_regime(rows)
        self.assertIsNotNone(result[19]['weighted_return_20d'])
        self.assertIsNone(result[20]['weighted_return_20d'])
        self.assertIsNone(result[21]['weighted_return_20d'])
        self.assertEqual(result[21]['regime'], 'WARMUP')


if __name__ == '__main__':
    unittest.main()
