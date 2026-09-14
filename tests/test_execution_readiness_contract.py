from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / 'server'
if str(SERVER) not in sys.path:
    sys.path.insert(0, str(SERVER))

import execution_readiness as er


def text(path):
    return (ROOT / path).read_text(encoding='utf-8')


class ExecutionReadinessContractTests(unittest.TestCase):
    def test_module_is_read_only_and_orderless(self):
        src = text('server/execution_readiness.py')
        for forbidden in (
            'nhplug', '/api/nh/order', 'paper_enter(', 'sqlite3',
            'requests.', 'httpx.', 'subprocess.', 'ENABLE_TRADING=True',
        ):
            self.assertNotIn(forbidden, src)
        report = er.readiness_report()
        self.assertFalse(report['realOrderEnabled'])
        self.assertFalse(report['microLiveReady'])
        self.assertTrue(report['researchOnly'])
        self.assertFalse(report['safety']['sendsOrders'])
        self.assertFalse(report['safety']['readsCredentials'])
        self.assertFalse(report['safety']['mutatesControl'])
        self.assertFalse(report['safety']['mutatesPaper'])

    def test_lifecycle_models_partial_fill_and_terminal_immutability(self):
        path = [
            ('INITIALIZED', 'SUBMITTED'),
            ('SUBMITTED', 'ACCEPTED'),
            ('ACCEPTED', 'PARTIALLY_FILLED'),
            ('PARTIALLY_FILLED', 'PARTIALLY_FILLED'),
            ('PARTIALLY_FILLED', 'FILLED'),
        ]
        for current, new in path:
            self.assertTrue(er.transition_allowed(current, new))
            self.assertEqual(er.apply_transition(current, new), new)
        for terminal in er.TERMINAL_STATES:
            for target in er.ORDER_STATES:
                self.assertFalse(er.transition_allowed(terminal, target))
        with self.assertRaises(ValueError):
            er.apply_transition('FILLED', 'ACCEPTED')

    def test_cancel_path_is_explicit_and_fail_closed(self):
        self.assertTrue(er.transition_allowed('ACCEPTED', 'CANCEL_PENDING'))
        self.assertTrue(er.transition_allowed('CANCEL_PENDING', 'CANCELED'))
        self.assertTrue(er.transition_allowed('CANCEL_PENDING', 'FILLED'))
        self.assertFalse(er.transition_allowed('INITIALIZED', 'FILLED'))
        with self.assertRaises(ValueError):
            er.apply_transition('INITIALIZED', 'FILLED')
        with self.assertRaises(ValueError):
            er.transition_allowed('UNKNOWN', 'FILLED')

    def test_client_order_key_is_deterministic_and_attempt_scoped(self):
        kwargs = dict(
            trading_date='2026-09-15',
            code='005930',
            side='BUY',
            signal_bucket='2026-09-15T10:05:00+09:00',
        )
        first = er.client_order_key(**kwargs)
        retry = er.client_order_key(**kwargs)
        replacement = er.client_order_key(**kwargs, attempt=1)
        self.assertEqual(first, retry)
        self.assertNotEqual(first, replacement)
        self.assertTrue(first.startswith('std-'))
        with self.assertRaises(ValueError):
            er.client_order_key(**{**kwargs, 'code': 'ABC'})
        with self.assertRaises(ValueError):
            er.client_order_key(**{**kwargs, 'side': 'HOLD'})

    def test_reconciliation_surfaces_mismatches_without_repair(self):
        internal = [
            {'clientOrderKey': 'k1', 'status': 'ACCEPTED', 'qty': 10, 'filledQty': 0},
            {'clientOrderKey': 'k2', 'status': 'PARTIALLY_FILLED', 'qty': 5, 'filledQty': 2},
        ]
        broker = [
            {'clientOrderKey': 'k1', 'status': 'FILLED', 'qty': 10, 'filledQty': 10},
            {'clientOrderKey': 'k3', 'status': 'ACCEPTED', 'qty': 1, 'filledQty': 0},
        ]
        result = er.reconcile_orders(internal, broker)
        self.assertFalse(result['ok'])
        self.assertTrue(result['readOnly'])
        self.assertEqual(result['missingInBroker'], ['k2'])
        self.assertEqual(result['externalOnly'], ['k3'])
        self.assertEqual(result['statusMismatch'][0]['clientOrderKey'], 'k1')
        self.assertEqual(result['fillMismatch'][0]['clientOrderKey'], 'k1')
        self.assertEqual(internal[0]['status'], 'ACCEPTED')

    def test_clean_reconciliation_is_identified(self):
        rows = [{'clientOrderKey': 'k1', 'status': 'FILLED', 'qty': 10, 'filledQty': 10}]
        result = er.reconcile_orders(rows, [dict(rows[0])])
        self.assertTrue(result['ok'])
        self.assertEqual(result['statusMismatch'], [])
        self.assertEqual(result['fillMismatch'], [])

    def test_control_and_real_order_invariants_remain_unchanged(self):
        app = text('server/app.py').replace(' ', '')
        paper = text('server/paper_engine.py').replace(' ', '')
        self.assertIn("VERSION='0.8.0'", app)
        self.assertIn('ENABLE_TRADING=False', app)
        for marker in (
            'RISK_PER_TRADE=.0035', 'STOP_PCT=.010', 'TRAIL_ACTIVATE_PCT=.015',
            'TRAIL_PCT=.008', 'BREAKEVEN_ACTIVATE_PCT=.008',
            'MAX_CONSECUTIVE_LOSSES=2', 'MAX_OPEN_POSITIONS=2',
            'MAX_DAILY_TRADES=8', 'DAILY_MAX_LOSS_PCT=.0075',
        ):
            self.assertIn(marker, paper)

    def test_reference_review_documents_license_boundary(self):
        doc = text('docs/OPEN_SOURCE_EXECUTION_REVIEW.md')
        self.assertIn('QuantConnect/Lean', doc)
        self.assertIn('NautilusTrader', doc)
        self.assertIn('OpenAlgo', doc)
        self.assertIn('No third-party implementation code is copied', doc)
        self.assertIn('REAL ORDER OFF', doc)


if __name__ == '__main__':
    unittest.main()
