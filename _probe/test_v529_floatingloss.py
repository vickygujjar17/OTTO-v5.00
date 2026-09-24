"""
v5.29 verification probe - 1% Max Floating Loss re-basis + non-permanent cut.

Pins the two behaviours the MQL5 compiler gate cannot exercise:

  A. ARITHMETIC (re-implemented below, mirrors otto.mq5 OnTick)
     1. equity >= balance  -> 0.00% (flat book, or open profit)
     2. equity <  balance  -> exact (balance - equity) / balance
     3. A flat book can never trip the rule on any equity level.
     4. A 1.50% peak retracement with NO position open must NOT trip it.
     5. The retired peak-equity measure DID trip on that same book -- pinned,
        so the regression this release removes cannot silently return.

  B. SOURCE (static slices of the shipped otto.mq5 / OttoDefines.mqh)
     6. The 1% branch measures balance-vs-equity, not the equity HWM.
     7. The 1% branch latches NOTHING: no g_totalDD_Halted, no "Halted" GV.
     8. The 1% branch still cancels pendings, closes the basket, and returns.
     9. The 5% trailing branch is unchanged and IS still the permanent halt.
    10. A 5% halt therefore still survives a restart; a floating cut leaves
        nothing for OnInit's OttoGvLoadFlag("Halted") to restore.
    11. OttoDefines documents the balance-vs-equity basis (the v5.22 text
        asserting the opposite is gone).
    12. The v5.24 "shared basis" claims in the trailing-HWM comments are gone.
    13. Version stamp 5.29 across all 10 files; 5.28 does not survive.

Pure static analysis of the shipped sources - no MT5 required.
"""

import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MQ5 = os.path.join(ROOT, "otto.mq5")
DEFS = os.path.join(ROOT, "OttoDefines.mqh")

ALL_FILES = ["otto.mq5", "COttoOrderManager.mqh", "COttoTradeManager.mqh",
             "COttoRiskManager.mqh", "COttoBlockManager.mqh", "COttoJournal.mqh",
             "COttoNewsFilter.mqh", "COttoCorrelationFilter.mqh",
             "COttoMarketStructure.mqh", "OttoDefines.mqh"]

SAFETY_FLOAT, SAFETY_TOTAL, SAFETY_DAILY = 1.0, 5.0, 3.0


def read(p):
    return io.open(p, encoding="utf-8", errors="replace", newline="").read()


# ----------------------------------------------------------------------
# 0. Re-implementation of the shipped measures.
# ----------------------------------------------------------------------
def floating_loss(balance, equity):
    """v5.29: unrealised loss on OPEN positions vs the CLOSED balance."""
    if balance <= 0 or equity >= balance:
        return 0.0
    return 100.0 * (balance - equity) / balance


def floating_trailing(equity_hwm, equity):
    """v5.22-v5.28: retracement from peak EQUITY (retired)."""
    if equity_hwm <= 0:
        return 0.0
    return 100.0 * (equity_hwm - equity) / equity_hwm


def total_dd(equity_hwm, equity):
    """v5.24: the 5% trailing measure, still in force."""
    if equity_hwm <= 0:
        return 0.0
    return 100.0 * (equity_hwm - equity) / equity_hwm


RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))


def checkf(name, got, want, tol=1e-9):
    check(name, abs(got - want) <= tol, "got=%.6f want=%.6f" % (got, want))


# ======================================================================
# A. ARITHMETIC
# ======================================================================
# 1-2. The measure itself.
checkf("flat book: balance == equity -> 0.0000%",
       floating_loss(100000, 100000), 0.0)
check("  ...and cannot breach", floating_loss(100000, 100000) < SAFETY_FLOAT,
      "0.00% < 1.00%")
checkf("open profit: equity ABOVE balance -> 0.0000% (floored)",
       floating_loss(100000, 108900), 0.0)
checkf("1% open loss: balance 100000 / equity 99000 -> 1.0000%",
       floating_loss(100000, 99000), 1.0)
check("  ...breaches exactly at the threshold",
      floating_loss(100000, 99000) >= SAFETY_FLOAT, "1.00% >= 1.00%")
checkf("just under: equity 99001 -> 0.9990%", floating_loss(100000, 99001), 0.999)
check("  ...does not breach", floating_loss(100000, 99001) < SAFETY_FLOAT,
      "0.999% < 1.00%")
checkf("2% open loss reads 2.0000%", floating_loss(100000, 98000), 2.0)
checkf("zero-balance guard -> 0.0000%", floating_loss(0, 50000), 0.0)
checkf("negative-balance guard -> 0.0000%", floating_loss(-1, 50000), 0.0)

# 3. A flat book never trips, at ANY equity level. This is the structural
#    guarantee the fix relies on: equity == balance whenever nothing is open.
for eq in (50000, 99000, 99999, 100000, 100001, 150000):
    check("flat book at equity %d never trips" % eq,
          floating_loss(eq, eq) < SAFETY_FLOAT, "equity == balance -> 0%")

# 4. THE regression case. Balance is a CLOSED 100000. Equity ratcheted to
#    101500 intraday (open profit), then gave back to 100000: a 1.4778%
#    retracement from the peak with NO losing position anywhere.
hwm, eq = 101500, 100000
checkf("1.4778% retracement from a 101500 peak",
       floating_trailing(hwm, eq), 1.477832512315271)
check("  ...the RETIRED measure trips on it",
      floating_trailing(hwm, eq) >= SAFETY_FLOAT, "the false positive")
checkf("  ...but v5.29 reads balance-vs-equity",
       floating_loss(100000, eq), 0.0)
check("  ...so the 1% rule does NOT trip (no open loss)",
      floating_loss(100000, eq) < SAFETY_FLOAT, "0.00% < 1.00%")

# 5. A 1.5% retracement where the book is flat and still in profit.
hwm, eq = 104000, 102440
checkf("104000 peak -> 102440 is a 1.5000% give-back",
       floating_trailing(hwm, eq), 1.5)
check("  ...retired measure trips", floating_trailing(hwm, eq) >= SAFETY_FLOAT)
check("  ...v5.29 stays silent on the flat book",
      floating_loss(eq, eq) < SAFETY_FLOAT, "equity == balance -> 0%")

# ======================================================================
# B. SOURCE
# ======================================================================
MQ5_T = read(MQ5)
DEFS_T = read(DEFS)
LINES = MQ5_T.split("\n")


def slice_between(start_marker, end_marker):
    """Text of the first region from start_marker up to end_marker."""
    a = MQ5_T.find(start_marker)
    if a < 0:
        return ""
    b = MQ5_T.find(end_marker, a)
    return MQ5_T[a:b] if b < 0 else MQ5_T[a:b]


# 6-8. The 1% branch: locate it by the v5.29 fix header, end at the 3% branch.
FLOAT_BRANCH = slice_between('// FIX (v5.29): "floating loss" is the UNREALISED loss',
                             "// 3% Max Daily Drawdown")
check("1% branch located in OnTick", len(FLOAT_BRANCH) > 200,
      "len=%d" % len(FLOAT_BRANCH))
check("the 1% header sits inside the slice",
      "// 1% Max Floating Loss Rule" in FLOAT_BRANCH)

# 6. Measures balance-vs-equity (NOT the equity HWM).
# NOTE (v5.29 hotfix): `balance` and `equity` are now declared ONCE at the top of
# the Step-0 block (see the declaration-integrity checks in section 14), not
# inside this branch. The branch therefore consumes the variables; the
# AccountInfoDouble(ACCOUNT_BALANCE) origin is asserted at block scope below.
check("1% branch computes from a `balance` variable",
      "balance" in FLOAT_BRANCH)
check("the `balance` variable originates from AccountInfoDouble(ACCOUNT_BALANCE)",
      "double balance = AccountInfoDouble(ACCOUNT_BALANCE)" in MQ5_T)
check("1% branch computes (balance - equity) / balance",
      "(balance - equity) / balance" in FLOAT_BRANCH)
check("1% branch floors profit at 0 (equity >= balance -> 0.0)",
      "equity < balance" in FLOAT_BRANCH and "0.0;" in FLOAT_BRANCH)
# Strip // comments (the v5.29 note deliberately NAMES the retired HWM) and
# assert no CODE line in the branch actually reads the high-water mark.
FLOAT_CODE = "\n".join(l.split("//")[0] for l in FLOAT_BRANCH.split("\n"))
check("1% branch does NOT reference the equity high-water mark",
      "g_equityHighWaterMark" not in FLOAT_CODE,
      "no HWM reference on any executable line")
check("1% branch's only drawdown measure is floatingLoss",
      FLOAT_CODE.count("floatingLoss") >= 2
      and "totalDD" not in FLOAT_CODE,
      "guard + Print use floatingLoss; totalDD belongs to the 5% rule")

# 7. Latches nothing.
check("1% branch does NOT set g_totalDD_Halted",
      "g_totalDD_Halted = true" not in FLOAT_BRANCH)
check("1% branch does NOT persist a Halted global",
      'OttoGvStoreFlag("Halted"' not in FLOAT_BRANCH)
check("1% branch does NOT touch the daily pause latch",
      "g_dailyDD_Paused = true" not in FLOAT_BRANCH)

# 8. Still protects capital and resumes trading.
check("1% branch cancels pending orders",
      "CancelAllPendingOrders()" in FLOAT_BRANCH)
check("1% branch closes the whole basket",
      'CloseEntireBasket("1% Max Floating Loss Cut")' in FLOAT_BRANCH)
check("1% branch returns for this tick only",
      "return;   // this tick only; no latch, no cooldown" in FLOAT_BRANCH)
check("the cut is logged as a cut, not a halt",
      "FLOATING LOSS CUT:" in FLOAT_BRANCH
      and "basket closed, trading continues" in FLOAT_BRANCH)
check("the old permanent-halt wording is gone",
      "EA HALTED" not in FLOAT_BRANCH
      and "1% Max Floating Loss Breach" not in MQ5_T)

# 9. The 5% trailing branch keeps the permanent halt intact.
TOTAL_BRANCH = slice_between("// 5% Trailing Total Drawdown",
                             "// STEP 1: News Filter")
check("5% branch located", len(TOTAL_BRANCH) > 200, "len=%d" % len(TOTAL_BRANCH))
check("5% branch arms g_totalDD_Halted",
      "g_totalDD_Halted = true" in TOTAL_BRANCH)
check("5% branch persists the Halted global",
      'OttoGvStoreFlag("Halted", true)' in TOTAL_BRANCH)
check("5% branch closes the basket",
      'CloseEntireBasket("Total Trailing DD Halt")' in TOTAL_BRANCH)
check("5% branch is still announced as PERMANENTLY halted",
      "EA PERMANENTLY HALTED" in TOTAL_BRANCH)
check("the g_totalDD_Halted early-return still guards OnTick",
      "if(g_totalDD_Halted) return;" in MQ5_T)

# 10. Restart semantics: only the 5% halt has anything to restore. The 1%
#     branch must not have written the GV, so OnInit reads the pre-existing
#     value (false) and trading resumes.
check("OnInit still restores the (5%-only) halt latch",
      'g_totalDD_Halted = OttoGvLoadFlag("Halted", false);' in MQ5_T)
check("Paused / Halted GVs are the only latches persisted",
      'OttoGvStoreFlag("Halted"' in MQ5_T
      and 'OttoGvStoreFlag("Paused"' in MQ5_T)

# 11. OttoDefines documents the balance-vs-equity basis; the v5.22 claim that
#     the measure was a trailing retracement from the equity HWM is gone.
check("OttoDefines annotates the rule as v5.29",
      "// FIX (v5.29): GFT 1% max FLOATING loss." in DEFS_T)
check("OttoDefines states the balance-vs-equity basis",
      "(balance - equity) /" in DEFS_T)
check("OttoDefines states the flat-book guarantee (equity == balance)",
      "equity == balance" in DEFS_T)
check("OttoDefines no longer calls it a TRAILING retracement",
      "Measured as a TRAILING retracement" not in DEFS_T
      and "from the peak-equity high-water mark (not raw balance-vs-equity)" not in DEFS_T)
check("OttoDefines states that only the 5% rule halts for good",
      "only the 5% trailing total-DD rule halts for good" in DEFS_T)
check("OttoDefines records the v5.22-v5.28 basis it replaced",
      "The v5.22 build instead measured a TRAILING retracement" in DEFS_T)

# 12. The stale v5.24 "shared basis" claims are gone from otto.mq5.
check("trailing-HWM comment no longer claims a shared 1%/5% basis",
      "sharing one basis" not in MQ5_T
      and "with the 1% floating rule. Only ratchets UP" not in MQ5_T)
check("trailing-HWM comment now scopes the HWM to the 5% rule",
      "basis of the 5% trailing total-DD rule ALONE" in MQ5_T)
check("init comment no longer claims the 1% rule shares the HWM",
      "shared by the 5% trailing DD and the 1% floating rule)" not in MQ5_T
      and "the 1% floating rule no longer shares it, v5.29" in MQ5_T)
check("halt-latch comment scopes the latch to the 5% check",
      "this latch is now armed ONLY by the 5% trailing total-DD" in MQ5_T)
check("init log labels the floating input as % of balance",
      '"% | Floating: ", SafetyMaxFloatingLoss, "% of balance");' in MQ5_T)

# 13. Version stamps.
for f in ALL_FILES:
    txt = read(os.path.join(ROOT, f))
    check("5.32 stamped in %s" % f, '#property version   "5.32"' in txt)
    check("no 5.28/5.29/5.30/5.31 property stamp in %s" % f,
          '#property version   "5.28"' not in txt
          and '#property version   "5.29"' not in txt
          and '#property version   "5.30"' not in txt
          and '#property version   "5.31"' not in txt)
check("OttoDefines banner names v5.32", "OTTO EA v5.32" in DEFS_T)
check("OttoDefines description names v5.32",
      '#property description "OTTO v5.32' in DEFS_T)
check("otto.mq5 port banner reads v5.32",
      "Pine Script Master Build Port (v5.32)" in MQ5_T)
check("otto.mq5 init banner reads v5.32", "OTTO EA v5.32" in MQ5_T)

# 14. DECLARATION INTEGRITY of the Step-0 safety block (regression guard).
#     Rationale: the v5.29 patch rewrote the 1% rule inside this block and, as a
#     side effect of replacing the comment span beneath the opening brace, deleted
#     `double equity = AccountInfoDouble(ACCOUNT_EQUITY);`. Nothing in the test
#     suite noticed, because every earlier check tests BEHAVIOUR (regexes over the
#     source) rather than COMPILABILITY. The compiler is the only authority on
#     undeclared identifiers, and the gate that should have run it was pointed at
#     another directory (see build_check.ps1). These checks assert the property
#     that actually failed: every identifier the block uses is declared exactly
#     once, and declared before first use.
import re as _re

SAFETY_BLOCK = slice_between("// STEP 0: PROP FIRM SAFETY CHECKS",
                             "// STEP 1: News Filter")
check("Step-0 safety block located", len(SAFETY_BLOCK) > 2000,
      "len=%d" % len(SAFETY_BLOCK))

# Strip // comments so prose that NAMES an identifier is not counted as a use.
SAFETY_CODE = "\n".join(l.split("//")[0] for l in SAFETY_BLOCK.split("\n"))


def _decl_count(ident):
    return len(_re.findall(r"\b(?:double|int|long|bool|string|datetime)\s+%s\b"
                           % ident, SAFETY_CODE))


def _use_count(ident):
    # \b treats '_' as a word char, so this correctly ignores g_equityHighWaterMark.
    return len(_re.findall(r"\b%s\b" % ident, SAFETY_CODE))


def _first_decl_pos(ident):
    m = _re.search(r"\b(?:double|int|long|bool|string|datetime)\s+%s\b" % ident,
                   SAFETY_CODE)
    return m.start() if m else -1


def _first_use_pos(ident):
    m = _re.search(r"\b%s\b" % ident, SAFETY_CODE)
    return m.start() if m else -1


# `equity` was the identifier orphaned by v5.29: 1 declaration + 8 uses. The
# 5.29 commit shipped with 0 declarations and exactly 8 uses, which is why the
# compiler emitted exactly 8 x "error 256: undeclared identifier 'equity'".
check("`equity` is declared exactly once in the safety block",
      _decl_count("equity") == 1, "found %d" % _decl_count("equity"))
check("`balance` is declared exactly once in the safety block",
      _decl_count("balance") == 1, "found %d" % _decl_count("balance"))
check("`equity` has 8 uses + 1 declaration (the 8 that failed to compile)",
      _use_count("equity") == 9, "found %d" % _use_count("equity"))
check("`equity` is declared BEFORE its first use",
      _first_decl_pos("equity") >= 0
      and _first_decl_pos("equity") <= _first_use_pos("equity"))
check("`balance` is declared BEFORE its first use",
      _first_decl_pos("balance") >= 0
      and _first_decl_pos("balance") <= _first_use_pos("balance"))

# The declaration must read the right account property, and both must be read
# from the terminal rather than hardcoded or carried over from another scope.
check("the `equity` declaration reads AccountInfoDouble(ACCOUNT_EQUITY)",
      _re.search(r"double\s+equity\s*=\s*AccountInfoDouble\(ACCOUNT_EQUITY\)",
                 SAFETY_CODE) is not None)
check("the `balance` declaration reads AccountInfoDouble(ACCOUNT_BALANCE)",
      _re.search(r"double\s+balance\s*=\s*AccountInfoDouble\(ACCOUNT_BALANCE\)",
                 SAFETY_CODE) is not None)

# No OTHER identifier in the block may be used without a declaration in the
# block, in the file's globals, or as a known MQL5/OTTO API. This is a coarse
# net: it flags the specific class of defect (a use with no local declaration
# and no global), without pretending to reimplement the MQL5 compiler.
GLOBALS = set(_re.findall(r"^\s*(?:static\s+)?(?:\w+\s+)+(g_\w+)\s*(?:=|;|\[)",
                          MQ5_T, _re.M))
LOCAL_DECLS = set(_re.findall(r"\b(?:double|int|long|bool|string|datetime)\s+"
                              "(\w+)\s*=", SAFETY_CODE))
check("every g_* identifier used in the block has a file-scope declaration",
      all(v in GLOBALS for v in _re.findall(r"\b(g_\w+)\b", SAFETY_CODE)),
      "missing: %s" % sorted({v for v in _re.findall(r"\b(g_\w+)\b", SAFETY_CODE)
                              if v not in GLOBALS}))

# Positive control: the two account values must NOT be re-declared later in the
# block, which would be a duplicate-definition error rather than a missing one.
check("neither account value is re-declared further down the block",
      _decl_count("equity") == 1 and _decl_count("balance") == 1,
      "one declaration each, at the top of the block")


def main():
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    for name, ok, detail in RESULTS:
        flag = "PASS" if ok else "FAIL"
        line = "  [%s] %s" % (flag, name)
        if detail and not ok:
            line += "   (%s)" % detail
        print(line)
    print("-" * 74)
    print("%d / %d checks passed" % (passed, total))
    print("*** ALL CHECKS PASSED ***" if passed == total
          else "*** FAILURES PRESENT ***")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
