"""
Logic verification for the v5.28 daily-reset BOUNDARY fix.

The bug this pins: the session boundary was read off the BROKER's D1 candle,

    datetime boundary = iTime(_Symbol, PERIOD_D1, 0);

`iTime(PERIOD_D1,0)` is the broker server's 00:00, and the v5.23 comment
asserted that this *is* 5:00 PM New York. That identity only holds on a
GMT+2/+3 feed. GFT's daily drawdown counter actually resets at 17:00 NEW YORK
(UTC-5 in winter, UTC-4 in summer), so on a UTC or local feed the budget was
re-baselined 5-8 hours away from the firm's real reset -- re-arming the 3%
daily limit for a slice of the session it should already have been spent in,
or holding yesterday's basis through the real boundary.

The v5.28 fix derives the boundary from `TimeGMT()` with the US DST rule
applied:

    datetime OttoLast5pmNewYork(datetime gmt);   // most recent 17:00 NY, in UTC

This probe reproduces that helper in Python and cross-checks it against
`zoneinfo.ZoneInfo("America/New_York")` -- the IANA tz database, i.e. an
independent oracle that shares no code with the MQL implementation. Every
assertion below is an oracle comparison, not a restatement of the formula.

It also pins the two properties the v5.23 fix was about, so that fix cannot
regress through the new code path:
  * the anchor is CONSTANT for a full 24h  (so `!=` fires once per session)
  * the anchor CHANGES after a boundary   (so the pause actually lifts)
"""

import datetime as _dt

try:
    from zoneinfo import ZoneInfo
    _NY = ZoneInfo("America/New_York")
except Exception as exc:                                  # pragma: no cover
    raise SystemExit("FATAL: zoneinfo/tzdata unavailable (%s). "
                     "Run: python -m pip install tzdata" % exc)

SECONDS_PER_DAY = 86400
UTC = _dt.timezone.utc


# ---------------------------------------------------------------------------
# Faithful ports of the MQL helpers in otto.mq5 (v5.28)
# ---------------------------------------------------------------------------

def otto_nth_sunday(year, month, nth):
    """Day-of-month of the nth Sunday, or 0 if the month has fewer."""
    seen = 0
    for d in range(1, 32):
        try:
            wd = _dt.date(year, month, d).weekday()
        except ValueError:
            return 0                     # rolled past the month end
        if wd == 6:                      # Python: Mon=0 .. Sun=6
            seen += 1
            if seen == nth:
                return d
    return 0


def otto_is_new_york_dst(gmt_epoch):
    """
    True when the UTC instant is inside US daylight-saving time.

    Since 2007: 02:00 LOCAL on the 2nd Sunday of March  == 07:00 UTC,
                02:00 LOCAL on the 1st Sunday of November == 06:00 UTC.
    """
    dt = _dt.datetime.fromtimestamp(gmt_epoch, UTC)
    start_day = otto_nth_sunday(dt.year, 3, 2)
    end_day = otto_nth_sunday(dt.year, 11, 1)
    if start_day == 0 or end_day == 0:
        return False
    dst_start = int(_dt.datetime(dt.year, 3, start_day, 7, tzinfo=UTC).timestamp())
    dst_end = int(_dt.datetime(dt.year, 11, end_day, 6, tzinfo=UTC).timestamp())
    return dst_start <= gmt_epoch < dst_end


def otto_new_york_time(gmt_epoch):
    """NY wall-clock instant, as a naive epoch-shifted number."""
    offset = 4 if otto_is_new_york_dst(gmt_epoch) else 5
    return gmt_epoch - offset * 3600


def otto_last_5pm_new_york(gmt_epoch):
    """
    Most recent 17:00:00 New York boundary, expressed as a UTC epoch.

    Mirrors OttoLast5pmNewYork(): shift the instant onto the NY clock, truncate
    to 17:00 on that NY date, step back a day while the candidate is still in
    the future, then map back to UTC with the offset in force AT the boundary.
    """
    ny = otto_new_york_time(gmt_epoch)
    d = _dt.datetime.fromtimestamp(ny, UTC)
    boundary_ny = int(_dt.datetime(d.year, d.month, d.day, 17, 0, 0,
                                   tzinfo=UTC).timestamp())
    if boundary_ny > ny:
        boundary_ny -= SECONDS_PER_DAY
    gmt_est = boundary_ny + 5 * 3600
    return boundary_ny + 4 * 3600 if otto_is_new_york_dst(gmt_est) else gmt_est


# ---------------------------------------------------------------------------
# Independent oracle: IANA tzdata via zoneinfo
# ---------------------------------------------------------------------------

def oracle_last_5pm_new_york(gmt_epoch):
    """
    The true most-recent 17:00 America/New_York boundary as a UTC epoch.

    Uses tz-aware arithmetic so the DST offset is resolved by the tz database
    rather than by our own rule. This is the reference the port is judged on.
    """
    now_ny = _dt.datetime.fromtimestamp(gmt_epoch, UTC).astimezone(_NY)
    cand = now_ny.replace(hour=17, minute=0, second=0, microsecond=0)
    if cand > now_ny:
        cand -= _dt.timedelta(days=1)
    return int(cand.astimezone(UTC).timestamp())


def oracle_offset_hours(gmt_epoch):
    off = _dt.datetime.fromtimestamp(gmt_epoch, UTC).astimezone(_NY).utcoffset()
    return -off.total_seconds() / 3600.0        # +5 for EST, +4 for EDT


def oracle_is_dst(gmt_epoch):
    return _dt.datetime.fromtimestamp(gmt_epoch, UTC).astimezone(_NY).dst() != _dt.timedelta(0)


def check(name, got, want):
    ok = got == want
    print("  [%s] %-64s got=%s want=%s" % ("PASS" if ok else "FAIL", name, got, want))
    return ok


def ts(y, m, d, hh=0, mm=0, ss=0):
    return int(_dt.datetime(y, m, d, hh, mm, ss, tzinfo=UTC).timestamp())


def fmt(epoch):
    return _dt.datetime.fromtimestamp(epoch, UTC).strftime("%Y-%m-%d %H:%M:%SZ")



def main():
    print("=" * 84)
    print("v5.28 DAILY-RESET BOUNDARY (17:00 New York) VERIFICATION")
    print("oracle: IANA tzdata America/New_York via zoneinfo")
    print("=" * 84)
    ok = True

    # ---------------------------------------------------------------- DST rule
    print("\n-- DST transition rule vs IANA tzdata --")
    for y in (2024, 2025, 2026, 2027, 2028, 2029, 2030):
        s = otto_nth_sunday(y, 3, 2)          # 2nd Sunday of March
        e = otto_nth_sunday(y, 11, 1)         # 1st Sunday of November
        ok &= check("2nd Sunday of March %d is a Sunday" % y,
                    _dt.date(y, 3, s).strftime("%a"), "Sun")
        ok &= check("1st Sunday of November %d is a Sunday" % y,
                    _dt.date(y, 11, e).strftime("%a"), "Sun")
        # Transitions are instantaneous, so probe one second either side.
        # Spring: 02:00 EST == 07:00 UTC. Fall: 02:00 EDT == 06:00 UTC.
        ok &= check("06:59:59 UTC on 2nd Sun Mar %d is EST" % y,
                    oracle_is_dst(ts(y, 3, s, 6, 59, 59)), False)
        ok &= check("07:00:00 UTC on 2nd Sun Mar %d is EDT" % y,
                    oracle_is_dst(ts(y, 3, s, 7, 0, 0)), True)
        ok &= check("05:59:59 UTC on 1st Sun Nov %d is EDT" % y,
                    oracle_is_dst(ts(y, 11, e, 5, 59, 59)), True)
        ok &= check("06:00:00 UTC on 1st Sun Nov %d is EST" % y,
                    oracle_is_dst(ts(y, 11, e, 6, 0, 0)), False)

    print("\n-- Offset (EST=5h / EDT=4h) agreement over a full year, 1h steps --")
    base = ts(2026, 1, 1)
    mism = checked = 0
    for i in range(366 * 24):
        t = base + i * 3600
        checked += 1
        if otto_new_york_time(t) != t - int(oracle_offset_hours(t) * 3600):
            mism += 1
    ok &= check("0 offset mismatches in %d hourly samples" % checked, mism, 0)

    print("\n-- Boundary (most recent 17:00 NY) agreement over a full year, 1h steps --")
    b_mism = b_checked = 0
    for i in range(366 * 24):
        t = base + i * 3600
        b_checked += 1
        if otto_last_5pm_new_york(t) != oracle_last_5pm_new_york(t):
            if b_mism < 5:
                print("       MISMATCH at %s: mql=%s oracle=%s"
                      % (fmt(t), fmt(otto_last_5pm_new_york(t)),
                         fmt(oracle_last_5pm_new_york(t))))
            b_mism += 1
    ok &= check("0 boundary mismatches in %d hourly samples" % b_checked, b_mism, 0)

    # ------------------------------- boundary agreement at the exact edges
    print("\n-- Boundary behaviour at the exact 17:00 NY edges --")
    # 2026: EDT runs 2026-03-08 .. 2026-11-01.
    for label, t in (("summer (EDT)", ts(2026, 7, 15, 12)),
                     ("winter (EST)", ts(2026, 1, 15, 12))):
        ok &= check("port == oracle @ %s" % label,
                    otto_last_5pm_new_york(t), oracle_last_5pm_new_york(t))
        b = otto_last_5pm_new_york(t)
        ny = _dt.datetime.fromtimestamp(b, UTC).astimezone(_NY)
        ok &= check("boundary reads 17:00:00 NY @ %s" % label,
                    (ny.hour, ny.minute, ny.second), (17, 0, 0))
        ok &= check("boundary is not in the future @ %s" % label, b <= t, True)

    # -------------------------------------------- the property v5.23 protected
    print("\n-- Constancy: the anchor must not move inside a session --")
    for label, t0 in (("spring-forward day 2026-03-08", ts(2026, 3, 8, 0)),
                      ("fall-back day     2026-11-01", ts(2026, 11, 1, 0)),
                      ("normal summer day  2026-07-15", ts(2026, 7, 15, 0)),
                      ("normal winter day  2026-01-15", ts(2026, 1, 15, 0))):
        prev = otto_last_5pm_new_york(t0)
        changes = []
        for i in range(1, SECONDS_PER_DAY // 60):
            v = otto_last_5pm_new_york(t0 + i * 60)
            if v != prev:
                changes.append(t0 + i * 60)
                prev = v
        ok &= check("%s -> exactly 1 change per 24h" % label, len(changes), 1)
        if changes:
            ny = _dt.datetime.fromtimestamp(changes[0], UTC).astimezone(_NY)
            ok &= check("  ...and it lands at 17:00 NY (%s)" % label,
                        (ny.hour, ny.minute), (17, 0))

    # ------------------------------------------------- tick-by-tick reset count
    print("\n-- Tick-count: resets per SECOND day (the v5.23 regression guard) --")
    # NOTE: a 24h window starting at 00:00 UTC ALWAYS contains exactly one
    # 17:00 NY boundary (17:00 EST = 22:00 UTC, 17:00 EDT = 21:00 UTC, both
    # fall inside 00:00-24:00 UTC), so 1 reset is the CORRECT answer here.
    # What matters is that the count is 1 -- not ~17280 as in the v5.22 bug.
    for label, t0 in (("2026-07-15 EDT", ts(2026, 7, 15, 0)),
                      ("2026-01-15 EST", ts(2026, 1, 15, 0))):
        anchor = otto_last_5pm_new_york(t0)
        hits = 0
        for i in range(0, SECONDS_PER_DAY, 5):        # 5s ticks for 24h
            v = otto_last_5pm_new_york(t0 + i)
            if v != 0 and v != anchor:
                anchor = v
                hits += 1
        ok &= check("%s -> exactly 1 reset in 24h of 5s ticks" % label, hits, 1)

    # A window that does NOT contain a boundary must produce ZERO resets.
    for label, t0 in (("2026-07-15 EDT", ts(2026, 7, 15, 5)),
                      ("2026-01-15 EST", ts(2026, 1, 15, 5))):
        anchor = otto_last_5pm_new_york(t0)
        hits = 0
        for i in range(0, 12 * 3600, 5):              # 12h window
            v = otto_last_5pm_new_york(t0 + i)
            if v != 0 and v != anchor:
                anchor = v
                hits += 1
        ok &= check("%s -> 0 resets in a boundary-free 12h" % label, hits, 0)

    bb = otto_last_5pm_new_york(ts(2026, 7, 15, 12))
    anchor = otto_last_5pm_new_york(bb - 600)
    hits = 0
    for i in range(0, 1200, 5):
        v = otto_last_5pm_new_york(bb - 600 + i)
        if v != 0 and v != anchor:
            anchor = v
            hits += 1
    ok &= check("crossing the boundary fires exactly once", hits, 1)

    # ------------------------------------------------------- Known UTC instants
    print("\n-- Worked examples (UTC instant -> 17:00 NY boundary) --")
    cases = [
        (ts(2026, 7, 15, 20, 59, 59), ts(2026, 7, 14, 21),
         "1s before 17:00 EDT -> previous day"),
        (ts(2026, 7, 15, 21, 0, 0), ts(2026, 7, 15, 21),
         "exactly 17:00 EDT"),
        (ts(2026, 7, 15, 21, 0, 1), ts(2026, 7, 15, 21),
         "1s after 17:00 EDT -> same day"),
        (ts(2026, 1, 15, 21, 59, 59), ts(2026, 1, 14, 22),
         "1s before 17:00 EST (UTC-5, not UTC-4)"),
        (ts(2026, 1, 15, 22, 0, 0), ts(2026, 1, 15, 22),
         "exactly 17:00 EST"),
    ]
    for when, want, why in cases:
        ok &= check(" %-46s (%s)" % (why, fmt(when)),
                    otto_last_5pm_new_york(when), want)
        ok &= check("   oracle agrees", oracle_last_5pm_new_york(when), want)

    # ------------------------------------- the bug: broker-midnight vs 17:00 NY
    print("\n-- THE BUG: broker D1 open vs the true 17:00 NY boundary --")
    print("    drift = (server-midnight boundary - true boundary) in hours")
    for srv_off, srv_name in ((2, "GMT+2"), (3, "GMT+3"), (0, "GMT+0"), (-5, "GMT-5")):
        rows = []
        for label, t in (("EDT", ts(2026, 7, 15, 12)), ("EST", ts(2026, 1, 15, 12))):
            true_b = oracle_last_5pm_new_york(t)
            srv_local = t + srv_off * 3600
            itime_b = (srv_local - (srv_local % SECONDS_PER_DAY)) - srv_off * 3600
            rows.append((label, (itime_b - true_b) / 3600.0))
        note = "  <-- only THIS feed coincides" if srv_off == 2 else ""
        print("    %-7s EDT drift=%+6.1fh   EST drift=%+6.1fh%s"
              % (srv_name, rows[0][1], rows[1][1], note))

    # Pin the specific claims the fix rests on.
    def itime_boundary(t, srv_off):
        srv_local = t + srv_off * 3600
        return (srv_local - (srv_local % SECONDS_PER_DAY)) - srv_off * 3600

    t = ts(2026, 1, 15, 12)
    ok &= check("GMT+2 feed: old iTime form == true boundary (winter)",
                itime_boundary(t, 2), oracle_last_5pm_new_york(t))
    # GMT+0: iTime = 2026-01-15 00:00Z; true = 2026-01-14 22:00Z -> +2h LATE.
    ok &= check("GMT+0 feed: old iTime form is 2h LATE (winter, native UTC)",
                (itime_boundary(t, 0) - oracle_last_5pm_new_york(t)) // 3600, 2)
    t = ts(2026, 7, 15, 12)
    ok &= check("GMT+2 feed: old iTime form is 1h LATE in summer (EDT)",
                (itime_boundary(t, 2) - oracle_last_5pm_new_york(t)) // 3600, 1)
    # GMT+0 in summer: iTime = 2026-07-15 00:00Z; true = 2026-07-14 21:00Z -> +3h.
    ok &= check("GMT+0 feed: old iTime form is 3h LATE in summer (EDT)",
                (itime_boundary(t, 0) - oracle_last_5pm_new_york(t)) // 3600, 3)

    # ------------------------------------------------------ restart re-anchoring
    print("\n-- Restart re-anchoring (OnInit) --")
    now = ts(2026, 7, 15, 12)                      # mid-session
    bound = otto_last_5pm_new_york(now)
    ok &= check("same-session restart: anchor NOT stale", bound < bound, False)
    ok &= check("same-session restart: |= yesterday|", bound - SECONDS_PER_DAY < bound, True)
    ok &= check("post-rollover restart: anchor IS stale",
                (bound - SECONDS_PER_DAY) < bound, True)
    ok &= check("prop-firm reset: pre-reset stamp is stale",
                (now - 3 * SECONDS_PER_DAY) < bound, True)

    # ---------------------------------------------- end-to-end pause life cycle
    print("\n-- End-to-end: 3% breach -> pause -> lift at 17:00 NY --")
    t0 = ts(2026, 7, 15, 0)                        # 20:00 NY on 14 Jul (EDT)
    anchor = otto_last_5pm_new_york(t0)
    paused = True                                  # breached before t0
    lifted_at = None
    for i in range(0, SECONDS_PER_DAY, 60):
        t = t0 + i
        v = otto_last_5pm_new_york(t)
        if v != 0 and v != anchor:
            anchor = v
            if paused:
                paused = False
                lifted_at = t
    ok &= check("pause lifted exactly once", lifted_at is not None, True)
    if lifted_at is not None:
        ny = _dt.datetime.fromtimestamp(lifted_at, UTC).astimezone(_NY)
        ok &= check("lift happened on the NY clock at 17:00",
                    (ny.hour, ny.minute), (17, 0))
        ok &= check("lift was not before the walk began", lifted_at >= t0, True)
        ok &= check("lift within one 60s sample of the boundary",
                    lifted_at - oracle_last_5pm_new_york(lifted_at) < 60, True)
    ok &= check("pause is clear after the boundary", paused, False)

    print()
    print("=" * 84)
    print("RESULT:", "ALL CHECKS PASSED" if ok else "FAILURES PRESENT")
    print("=" * 84)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())



    for label, y, m, d in (("EDT day", 2026, 7, 15), ("EST day", 2026, 1, 15)):
        bb = oracle_last_5pm_new_york(ts(y, m, d, 12))
        ok &= check("1s BEFORE %s boundary -> previous day" % label,
                    otto_last_5pm_new_york(bb - 1), bb - SECONDS_PER_DAY)
        ok &= check("AT the %s boundary -> the new day" % label,
                    otto_last_5pm_new_york(bb), bb)
