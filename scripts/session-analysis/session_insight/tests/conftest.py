"""Put the session_insight package dir on sys.path so its stdlib-style modules
(schema, scrub, select, prepare, consolidate, write, cli) import directly — the
same idiom the sibling collector/validation test suites use for their scripts."""
import sys
from pathlib import Path

_PKG = Path(__file__).resolve().parent.parent   # scripts/session-analysis/session_insight
if str(_PKG) not in sys.path:
    sys.path.insert(0, str(_PKG))
