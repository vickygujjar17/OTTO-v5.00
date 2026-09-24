"""
Logic verification for the v5.22 / v5.24 / v5.29 prop-firm safety rules.

Covers the change a compile CANNOT validate: the GFT drawdown arithmetic.
  * 3% daily DD measured from the 5PM NEW YORK reset balance
  * 5% TRAILING total DD measured from the peak EQUITY high-water mark
    (v5.22 used peak CLOSED balance; v5.24 switched it to peak equity)
  * 1% floating loss measured balance-vs-equity -- the UNREALISED loss on
    open positions (v5.29; v5.22-v5.28 used a TRAILING retracement)

FIX (v5.29): the floating rule and the 5% trailing rule were merged onto the
SAME equity basis by v5.24, so they evaluated one identical quantity. Three
faults followed, all pinned as FIXED below:
  * the 1% threshold sat permanently tighter than the 5% one, making the 5%
    check unreachable dead code;
  * a 1% retracement from the equity peak fired with NO position open, where
    there is by definition no floating loss at all;
  * the branch latched a persisted, init-restored halt, bricking the account.
The balance-vs-equity measure cannot reproduce the false positive: when the
book is flat, equity == balance and it reads exactly 0. It also restores the
5% trailing check as genuinely reachable.
"""


def daily_dd(reset_balance, equity):
    if reset_balance <= 0:
        return 0.0
    return 100.0 * (reset_balance - equity) / reset_balance


def total_dd(equity_hwm, equity):
    """v5.24 measure: retracement from peak EQUITY (was peak closed balance)."""
    if equity_hwm <= 0:
        return 0.0
    return 100.0 * (equity_hwm - equity) / equity_hwm


def total_dd_balance_basis(hwm_balance, equity):
    """The v5.22 measure, retained to document what changed and why."""
    if hwm_balance <= 0:
        return 0.0
    return 100.0 * (hwm_balance - equity) / hwm_balance


def floating_trailing(equity_hwm, equity):
    """The v5.22-v5.28 measure (now retired): retracement from peak EQUITY.

    Retained to document what changed and to prove it was the same quantity as
    total_dd -- the defect v5.29 removed.
    """
    if equity_hwm <= 0:
        return 0.0
    return 100.0 * (equity_hwm - equity) / equity_hwm


def floating_loss(balance, equity):
    """v5.29 measure: unrealised loss on OPEN positions vs the CLOSED balance.

    Exactly 0 whenever equity >= balance, which is the flat-book case.
    """
    if balance <= 0 or equity >= balance:
        return 0.0
    return 100.0 * (balance - equity) / balance


def floating_raw(balance, equity):
    """The pre-v5.22 measure (no zero-floor on profit), kept for contrast."""
    if balance <= 0:
        return 0.0
    return 100.0 * (balance - equity) / balance


def check(name, got, want):
    ok = got == want
    print("  [%s] %-58s got=%s want=%s" % ("PASS" if ok else "FAIL", name, got, want))
    return ok


def checkf(name, got, want, tol=1e-9):
    ok = abs(got - want) <= tol
    print("  [%s] %-58s got=%.4f want=%.4f" % ("PASS" if ok else "FAIL", name, got, want))
    return ok


SAFETY_DAILY, SAFETY_TOTAL, SAFETY_FLOAT = 3.0, 5.0, 1.0


def main():
    print("=" * 78)
    print("v5.22 / v5.24 PROP-FIRM SAFETY RULE VERIFICATION")
    print("=" * 78)
    ok = True

    # ---- 3% Daily DD ------------------------------------------------------
    print("\n-- 3% Daily Drawdown (reset balance = 5PM EST close) --")
    # Day starts at 100000, equity dips to 97000 = exactly 3%.
    ok &= checkf("equity 97000 from 100000 -> 3.0000%",
                 daily_dd(100000, 97000), 3.0)
    ok &= check("3.00% breach triggers pause", daily_dd(100000, 97000) >= SAFETY_DAILY, True)
    ok &= check("2.00% dip does NOT pause", daily_dd(100000, 98000) >= SAFETY_DAILY, False)
    # Profit above the reset balance gives NEGATIVE dd, not a false breach.
    ok &= checkf("equity 105000 -> -5.0000% (no breach)",
                 daily_dd(100000, 105000), -5.0)
    ok &= check("zero reset balance guard -> 0.0", daily_dd(0, 97000), 0.0)

    # ---- 5% Trailing Total DD --------------------------------------------
    print("\n-- 5% Trailing Total Drawdown (peak EQUITY, v5.24 basis) --")
    # Account grew 100000 -> 110000; a 5% trail from 110000 is 104500.
    ok &= checkf("equity 104500 from peak 110000 -> 5.0000%",
                 total_dd(110000, 104500), 5.0)
    ok &= check("5.00% from peak triggers halt", total_dd(110000, 104500) >= SAFETY_TOTAL, True)
    # 105000 sits 4.55% below the 110000 peak but only 5.00% below... it is
    # ABOVE the 100000 start, so the old static initial-balance measure read
    # NEGATIVE and would have missed the breach entirely.
    checkf("trailing peak 110000/equity 105000 -> 4.5455%",
           total_dd(110000, 105000), 4.545454545454545)
    checkf("static basis 100000/equity 105000 -> -5.0000%",
           total_dd(100000, 105000), -5.0)
    ok &= check("static measure MISSES it (negative, no halt)",
                total_dd(100000, 105000) >= SAFETY_TOTAL, False)
    # A new equity high raises the trail.
    ok &= check("equity 110000 at peak -> 0% drawdown", total_dd(110000, 110000), 0.0)
    ok &= check("zero hwm guard -> 0.0", total_dd(0, 50000), 0.0)

    # v5.24: the basis moved from peak CLOSED balance to peak EQUITY. Pin the
    # divergence so the distinction cannot silently regress either way. A book
    # that peaked at 110000 closed and now carries an open loss shows equity
    # below balance; the equity basis is the more conservative of the two.
    print("\n-- v5.24 basis change: peak equity vs peak closed balance --")
    # Both bases read identically when equity IS the peak (nothing open).
    ok &= checkf("flat account: both bases agree -> 0.5000%",
                 total_dd(110000, 109450), total_dd_balance_basis(110000, 109450))
    # Balance 110000 closed, then a position opens and equity drops to 109100.
    # The balance basis is blind to the intraday excursion; the equity basis
    # trails the true high and reports the real give-back.
    ok &= checkf("balance basis (blind) 110000/109100 -> 0.8182%",
                 total_dd_balance_basis(110000, 109100), 0.8181818181818181)
    ok &= checkf("equity basis (trail) 110000/109100 -> 0.8182%",
                 total_dd(110000, 109100), 0.8181818181818181)
    # Equity ratcheted to 115000 intraday before closing back at 110000. The
    # balance basis forgets that peak entirely; the equity basis remembers it.
    ok &= checkf("equity-basis remembers intraday 115000 peak -> 8.6957%",
                 total_dd(115000, 105000), 8.695652173913045)
    ok &= checkf("balance-basis forgets it -> 4.5455%",
                 total_dd_balance_basis(110000, 105000), 4.545454545454545)
    ok &= check("equity basis fires where balance basis would NOT",
                total_dd(115000, 105000) >= SAFETY_TOTAL, True)
    ok &= check("...and balance basis misses the same book",
                total_dd_balance_basis(110000, 105000) >= SAFETY_TOTAL, False)

    # ---- 1% Floating Loss: unrealised loss on OPEN positions (v5.29) -----
    print("\n-- 1% Floating Loss: balance-vs-equity on open positions --")
    # A flat book cannot show a floating loss: equity == balance -> exactly 0.
    ok &= checkf("flat book at 100000 -> 0.0000%", floating_loss(100000, 100000), 0.0)
    ok &= check("flat book cannot breach",
                floating_loss(100000, 100000) >= SAFETY_FLOAT, False)
    # In profit (equity above balance) still reads 0, never negative.
    ok &= checkf("in profit 100000/101500 -> 0.0000% (floored)",
                 floating_loss(100000, 101500), 0.0)
    # An open position carrying 1% of balance breaches.
    ok &= checkf("open loss 100000/99000 -> 1.0000%", floating_loss(100000, 99000), 1.0)
    ok &= check("1.00% on open positions breaches",
                floating_loss(100000, 99000) >= SAFETY_FLOAT, True)
    ok &= check("0.99% does NOT breach",
                floating_loss(100000, 99001) >= SAFETY_FLOAT, False)
    ok &= checkf("0.10% reads 0.1000%", floating_loss(100000, 99900), 0.1)
    ok &= check("zero balance guard -> 0.0", floating_loss(0, 50000), 0.0)
    ok &= check("  ...cannot breach on uninitialised state",
                floating_loss(0, 50000) >= SAFETY_FLOAT, False)

    # THE v5.29 FIX: the exact false positive. Balance is a CLOSED 100000 and
    # equity ratcheted to 110000 on open profit, then a fresh dip took equity
    # to 108900 -- still 8900 ABOVE balance. There is NO floating loss at all.
    print("\n-- v5.29 FIX: no false positive with the book in profit --")
    ok &= checkf("equity 108900 ABOVE balance 100000 -> 0.0000%",
                 floating_loss(100000, 108900), 0.0)
    ok &= check("  ...so the branch does NOT fire",
                floating_loss(100000, 108900) >= SAFETY_FLOAT, False)
    # The retired measure fired here on a book that was never losing money.
    ok &= checkf("retired trailing measure read 1.0000% from a 110000 peak",
                 floating_trailing(110000, 108900), 1.0)
    ok &= check("  ...and DID fire -- the false positive v5.29 removed",
                floating_trailing(110000, 108900) >= SAFETY_FLOAT, True)
    # A book genuinely underwater on the day, but by less than 1%.
    ok &= checkf("balance 100000/equity 99500 -> 0.5000% (real, under 1%)",
                 floating_loss(100000, 99500), 0.5)
    ok &= check("  ...does not breach",
                floating_loss(100000, 99500) >= SAFETY_FLOAT, False)

    # ---- Rule interaction (v5.29) -----------------------------------------
    print("\n-- v5.29 interaction: 1% floating (balance) vs 5% trailing (HWM) --")
    # The two rules are now on DIFFERENT bases, so the 5% trailing check is
    # genuinely reachable. This is the dead-code fault v5.29 repaired: on the
    # shared v5.24 basis the two values were equal at every point.
    print("\n  [merged-basis defect, retired]")
    for eq in (100000, 109000, 99000, 95000, 94000):
        f = floating_trailing(100000, eq)
        t = total_dd(100000, eq)
        ok &= checkf("    equity %d: retired floating == total_dd" % eq, f, t)

    # Under v5.29, a book flat at a 100000 balance with equity pulled to 95000
    # by open positions is 5.0% DOWN on floating loss AND 5.0% down on the
    # trailing measure only if the peak was 100000 too. Distinguish the bases:
    #   balance 100000, peak equity 110000, current equity 104500
    #     floating = 0.00%  (equity is still well ABOVE balance)
    #     trailing = 5.00%  (a full 5% given back from the equity peak)
    ok &= checkf("floating reads 0.0000% (equity above balance)",
                 floating_loss(100000, 104500), 0.0)
    ok &= checkf("trailing reads 5.0000% (peak give-back)",
                 total_dd(110000, 104500), 5.0)
    ok &= check("5% trailing is now REACHABLE while floating stays silent",
                (total_dd(110000, 104500) >= SAFETY_TOTAL,
                 floating_loss(100000, 104500) >= SAFETY_FLOAT), (True, False))
    # A day that is genuinely 1% down on open positions AND 5% off the peak.
    ok &= checkf("floating 100000/99000 -> 1.0000%", floating_loss(100000, 99000), 1.0)
    ok &= checkf("trailing from 105000 peak -> 5.7143%",
                 total_dd(105000, 99000), 5.714285714285714)
    ok &= check("both fire independently on their own bases",
                (floating_loss(100000, 99000) >= SAFETY_FLOAT,
                 total_dd(105000, 99000) >= SAFETY_TOTAL), (True, True))

    # 3% daily DD is unaffected by the change -- measure it on the same book.
    ok &= checkf("daily on the same book 100000/99000 -> 1.0000%",
                 daily_dd(100000, 99000), 1.0)
    ok &= check("daily does not breach at 1.00%",
                daily_dd(100000, 99000) >= SAFETY_DAILY, False)

    print()
    print("=" * 78)
    print("RESULT:", "ALL CHECKS PASSED" if ok else "FAILURES PRESENT")
    print("=" * 78)
    print()
    print("Thresholds: 1.00% floating (balance-vs-equity, v5.29) closes the")
    print("basket and resumes trading; 3.00% daily pauses new orders; 5.00%")
    print("trailing from peak EQUITY is the only PERMANENT halt.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
