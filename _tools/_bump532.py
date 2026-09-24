"""
One-shot v5.31 -> v5.32 version bump for the OTTO EA tree.

Rewrites #property version "5.31" -> "5.32" in otto.mq5 and every .mqh
header. Historical FIX annotations naming an older release must survive
untouched, so any line containing 'FIX (' is skipped outright rather than
pattern-matched.

The v5.32 release is a deliberate CONFIGURATION ROLLBACK plus one protocol
guard:
  * InpEnableVectorEngine / InpCancelOpposingPendings / InpEnableQuorumGuard
    all flip to default FALSE, restoring the loose classic model (open-
    position IsTradeVetoed correlation only).
  * PlaceLimitOrder() gains an MT5-exact Bid/Ask boundary guard so a limit
    whose entry has drifted past the live market is clamped or latched dead
    instead of being rejected with "[Invalid price]".

Only the release STAMP is touched here; the behavioural edits were applied by
_tools/_apply532_defs.py and _tools/_apply532_om.py.

Note: the untracked OTTO-v5.00/ snapshot is a DIFFERENT tree and is
deliberately NOT in FILES; only the live files at the repo root are bumped.

Reports every changed line so the diff can be eyeballed before commit.
"""

import io
import os
import re

OLD = "5.31"
NEW = "5.32"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FILES = ["otto.mq5", "COttoOrderManager.mqh", "COttoTradeManager.mqh",
         "COttoRiskManager.mqh", "COttoBlockManager.mqh", "COttoJournal.mqh",
         "COttoNewsFilter.mqh", "COttoCorrelationFilter.mqh",
         "COttoMarketStructure.mqh", "OttoDefines.mqh"]

PATTERNS = [
    (re.compile(r'(#property\s+version\s+")' + re.escape(OLD) + r'(")'),
     r'\g<1>' + NEW + r'\g<2>'),
]

# Human-facing banner / description strings that name the live release.
# 'FIX (vX)' historical annotations are already skipped by the caller, so the
# v5.31 ledger/SafeDeleteOrder comments in COttoOrderManager are never touched
# (they describe when the feature landed, not which release is shipped).
PATTERNS += [
    (re.compile(r'(Master Build \(v)' + re.escape(OLD) + r'(\))'),
     r'\g<1>' + NEW + r'\g<2>'),
    (re.compile(r'(OTTO EA v)' + re.escape(OLD) + r'( \u2014 28-Pair Institutional Master Build)'),
     r'\g<1>' + NEW + r'\g<2>'),
    (re.compile(r'(#property description "OTTO v)' + re.escape(OLD) + r'( \u2014)'),
     r'\g<1>' + NEW + r'\g<2>'),
    (re.compile(r'(\[9\] CURRENCY VECTOR & AFFINITY ENGINE \u2014 v)' + re.escape(OLD)),
     r'\g<1>' + NEW),
]

# Cosmetic banner: otto.mq5 line 4 names the port release in "Master Build
# Port (vX)" form, which the "Master Build (vX)" pattern above cannot match.
PORT_BANNER = (
    re.compile(r'(Pine Script Master Build Port \(v)' + re.escape(OLD) + r'(\))'),
    r'\g<1>' + NEW + r'\g<2>',
)


def bump(path):
    full = os.path.join(ROOT, path)
    if not os.path.exists(full):
        print("  MISSING  %s" % path)
        return 0
    text = io.open(full, encoding="utf-8", newline="").read()
    lines = text.split("\n")
    changed = 0
    for i, line in enumerate(lines):
        if "FIX (" in line:
            continue
        new_line = line
        for pat, rep in PATTERNS:
            new_line = pat.sub(rep, new_line)
        # The port banner is never inside a FIX annotation, but the guard above
        # is shared so the rule stays uniform.
        new_line = PORT_BANNER[0].sub(PORT_BANNER[1], new_line)
        if new_line != line:
            print("  %s:%d" % (path, i + 1))
            print("    - %s" % line.strip())
            print("    + %s" % new_line.strip())
            lines[i] = new_line
            changed += 1
    if changed:
        io.open(full, "w", encoding="utf-8", newline="").write("\n".join(lines))
    return changed


def main():
    print("=" * 74)
    print("v%s -> v%s VERSION BUMP" % (OLD, NEW))
    print("=" * 74)
    total = 0
    for f in FILES:
        total += bump(f)
    print()
    print("changed %d line(s)" % total)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
