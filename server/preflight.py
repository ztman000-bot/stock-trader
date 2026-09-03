"""Preflight for remote updates. No server threads are started here."""
from __future__ import annotations

import compileall
import importlib
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
ROOT = BASE.parent


def main():
    if not compileall.compile_dir(str(BASE), quiet=1, force=False):
        print('compileall failed', file=sys.stderr)
        return 2
    sys.path.insert(0, str(BASE))
    modules = [
        'collector', 'paper_engine', 'market_state_engine', 'decision_intelligence',
        'kr_1m_research', 'one_minute_exit_replay', 'robust_validation'
    ]
    for name in modules:
        importlib.import_module(name)
    print('PRECHECK_OK')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
