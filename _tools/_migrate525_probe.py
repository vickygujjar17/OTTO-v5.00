"""One-shot v5.28 migration of _probe/test_v525_persistence.py.

That probe is a behavioural MIRROR of the shipped otto.mq5 code, so when the
live code changes shape the mirror must change with it or the suite starts
asserting the wrong contract. v5.28 replaced the broker-midnight reset anchor
with a 17:00-New-York boundary and renamed the persist key
LastMid -> Last5pmReset, so the mirror is updated here:

  * `today_bar` (a broker D1 open) -> `ny_boundary` (17:00 NY, as a UTC epoch)
  * the staleness test drops its `today_bar != 0` guard, because the boundary
    is now computed from TimeGMT() and the MQL code has no equivalent guard
  * keys/strings renamed LastMid -> Last5pmReset

Every replacement is asserted to have applied, and the file is written back
byte-for-byte preserving CRLF.
"""

import io
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "_probe", "test_v525_persistence.py")

PAIRS = [
    # --- persist() --------------------------------------------------------
    ('def persist(store, daily, hwm, last_mid, paused, halted):',
     'def persist(store, daily, hwm, last_reset, paused, halted):'),
    ('    store[gv_name("LastMid")] = float(last_mid)',
     '    store[gv_name("Last5pmReset")] = float(last_reset)'),

    # --- on_init() --------------------------------------------------------
    ('def on_init(store, balance, equity, today_bar):',
     'def on_init(store, balance, equity, ny_boundary):'),
    ('    last_mid = int(gv_load_double(store, "LastMid", float(today_bar)))\r\n'
     '    stale = (today_bar != 0 and last_mid < today_bar)',
     '    # v5.28: the anchor is the most recent 17:00 New York boundary, so the\n'
     '    # value in force is supplied by the caller rather than read off a D1 bar.\n'
     '    last_reset = int(gv_load_double(store, "Last5pmReset", float(ny_boundary)))\n'
     '    stale = (last_reset < ny_boundary)'),
    ('    resume = (last_mid + 86400) if (paused and last_mid > 0) else 0',
     '    resume = (last_reset + 86400) if (paused and last_reset > 0) else 0'),
    ('    persist(store, daily, hwm, last_mid, paused, halted)',
     '    persist(store, daily, hwm, last_reset, paused, halted)'),
    ('    return {"daily": daily, "hwm": hwm, "last_mid": last_mid, "paused": paused,',
     '    return {"daily": daily, "hwm": hwm, "last_reset": last_reset, "paused": paused,'),

    # --- call sites and assertions ---------------------------------------
    ('today_bar=', 'ny_boundary='),
    ('s["last_mid"]', 's["last_reset"]'),
    ('s2["last_mid"]', 's2["last_reset"]'),
    ('gv_name("LastMid")', 'gv_name("Last5pmReset")'),

    # --- prose -----------------------------------------------------------
    ('"midnight stamp seeded to D1 bar"', '"17:00-NY stamp seeded to the live boundary"'),
    ('"midnight stamp advanced to DAY2"', '"17:00-NY stamp advanced to DAY2"'),
    ('"stale anchor DETECTED (stamp precedes today\'s bar)"',
     '"stale anchor DETECTED (stamp precedes the live boundary)"'),
    ('print("Persisted: DailyReset, HighWater, LastMid, Paused, Halted (per login).")',
     'print("Persisted: DailyReset, HighWater, Last5pmReset, Paused, Halted (per login).")'),
]


def main():
    text = io.open(PATH, encoding="utf-8", newline="").read()
    for old, new in PAIRS:
        n = text.count(old)
        if n == 0:
            print("  MISS   %r" % old[:70])
            continue
        text = text.replace(old, new)
        print("  ok %-3d %r" % (n, old[:70]))
    io.open(PATH, "w", encoding="utf-8", newline="").write(text)
    print("\nwrote %s" % PATH)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
