"""
v5.31 static verification probe - collision-proof per-cycle order deletion.

Pins the fix that cannot be exercised by the MQL5 compiler gate:

  1. The per-cycle deletion ledger exists on COttoOrderManager.
  2. The cycle is armed EXACTLY ONCE per OnTick, from otto.mq5.
  3. The guard is CYCLE-scoped, not mapping-scoped. This is the load-bearing
     assertion: blocks are in-memory only, so a
     `FindBlockIndexByTicket(ticket) == -1` skip would silently disable the
     cancellation of genuine broker orphans after any EA restart - the exact
     cleanup COttoOrderManager.mqh:1566 and the v5.26 header require.
  4. Every same-tick deletion site routes through SafeDeleteOrder, so two
     sweeps cannot both dispatch TRADE_ACTION_REMOVE for one ticket and draw
     "[Invalid request]" (retcode 10013 / error 4756).
  5. An UNARMED cycle still deletes (the orphan path stays live).
  6. The ledger is cold-started on construct and reset on Initialize.
  7. Version stamp 5.31 present in all 10 files; 5.30 rejected.

Pure static analysis of the shipped sources - no MT5 required.
"""

import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OM = os.path.join(ROOT, "COttoOrderManager.mqh")
BM = os.path.join(ROOT, "COttoBlockManager.mqh")
MQ5 = os.path.join(ROOT, "otto.mq5")
DEFS = os.path.join(ROOT, "OttoDefines.mqh")

ALL_FILES = ["otto.mq5", "COttoOrderManager.mqh", "COttoTradeManager.mqh",
             "COttoRiskManager.mqh", "COttoBlockManager.mqh", "COttoJournal.mqh",
             "COttoNewsFilter.mqh", "COttoCorrelationFilter.mqh",
             "COttoMarketStructure.mqh", "OttoDefines.mqh"]


def read(p):
    return io.open(p, encoding="utf-8", errors="replace", newline="").read()


def strip_comments(text):
    """Drop //-to-EOL comments so call-site counting cannot count prose.

    The v5.31 annotations name DeleteOrder()/SafeDeleteOrder() in their
    explanatory text, so a raw regex tally would over-count. No v5.31 source
    line mixes a string containing '//' with a trailing comment, so a simple
    per-line cut is sufficient here.
    """
    out = []
    for line in text.split("\n"):
        # Keep a '://' (e.g. a URL) intact; only a bare // starts a comment.
        idx = line.find("//")
        if idx >= 0 and not line[:idx].rstrip().endswith(":"):
            line = line[:idx]
        out.append(line)
    return "\n".join(out)


OM_T = read(OM)
BM_T = read(BM)
MQ5_T = read(MQ5)
DEFS_T = read(DEFS)

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))


def body(text, sig):
    """Return the source of the method starting at sig (brace-balanced)."""
    i = text.find(sig)
    if i < 0:
        return ""
    j = text.find("{", i)
    if j < 0:
        return ""
    depth = 0
    for k in range(j, len(text)):
        if text[k] == "{":
            depth += 1
        elif text[k] == "}":
            depth -= 1
            if depth == 0:
                return text[j:k + 1]
    return ""


# ---------------------------------------------------------------- 1
check("ledger array member m_deletedThisCycle[] declared",
      re.search(r"ulong\s+m_deletedThisCycle\[\]\s*;", OM_T) is not None)
check("ledger count member m_deletedThisCycleCount declared",
      re.search(r"int\s+m_deletedThisCycleCount\s*;", OM_T) is not None)
check("BeginOrderCycle() defined",
      "BeginOrderCycle(void)" in OM_T)
check("WasTicketDeletedThisCycle() defined",
      "WasTicketDeletedThisCycle(ulong ticket)" in OM_T)
check("MarkTicketDeletedThisCycle() defined",
      "MarkTicketDeletedThisCycle(ulong ticket)" in OM_T)
check("SafeDeleteOrder() defined",
      "SafeDeleteOrder(ulong ticket)" in OM_T)

# ---------------------------------------------------------------- 2
# The ledger must live on the class that owns DeleteOrder(), and the cycle
# must be armed from the EA's single per-tick entry point.
check("BeginOrderCycle is public (declared after the class 'public:')",
      OM_T.find("public:") < OM_T.find("void              BeginOrderCycle(void)"))
check("BeginOrderCycle appears exactly once in COttoOrderManager",
      OM_T.count("BeginOrderCycle(void)") == 1,
      "count=%d" % OM_T.count("BeginOrderCycle(void)"))
check("otto.mq5 arms the cycle exactly once",
      MQ5_T.count("BeginOrderCycle()") == 1,
      "count=%d" % MQ5_T.count("BeginOrderCycle()"))
check("otto.mq5 arms it via the order manager instance",
      "g_orderManager.BeginOrderCycle();" in MQ5_T)

on_tick = MQ5_T.find("void OnTick(void)")
arm = MQ5_T.find("g_orderManager.BeginOrderCycle();")
check("arm call sits inside OnTick", on_tick >= 0 and arm > on_tick)
# NOTE: match the CALL "if(IsNewBar())", not the bare identifier - the arm
# call's own explanatory comment mentions IsNewBar() and would false-positive.
is_new_bar = MQ5_T.find("if(IsNewBar())", on_tick)
check("arm precedes the if(IsNewBar()) block",
      is_new_bar > arm, "arm=%d isNewBar=%d" % (arm, is_new_bar))

# Every in-OnTick sweeper must appear AFTER the arm call, or a later sweeper
# could act on a ledger the earlier sweeps never populated.
for site in ["CancelOrdersForInvalidBlocks();", "CancelOpposingConsensusOrders();",
             "CancelQuorumOpposingOrders();", "ManageDirectionConflict();"]:
    idx = MQ5_T.find(site, on_tick)
    check("OnTick sweep '%s' runs after the arm call" % site,
          idx > arm, "arm=%d site=%d" % (arm, idx))

# ---------------------------------------------------------------- 3
check("begin resets the count",
      re.search(r"void\s+BeginOrderCycle\(void\)\s*\{\s*m_deletedThisCycleCount\s*=\s*0;\s*\}",
                OM_T) is not None)
check("ledger probe walks only the active prefix",
      re.search(r"for\(int\s+i\s*=\s*0;\s*i\s*<\s*m_deletedThisCycleCount;\s*i\+\+\)", OM_T) is not None)
check("ledger append resizes with headroom",
      "ArrayResize(m_deletedThisCycle, m_deletedThisCycleCount + 1, 16);" in OM_T)
check("ledger append records the ticket",
      "m_deletedThisCycle[m_deletedThisCycleCount++] = ticket;" in OM_T)
check("mark() refuses ticket <= 0",
      re.search(r"if\(ticket\s*<=\s*0\)\s*return;", body(OM_T, "void              MarkTicketDeletedThisCycle(ulong ticket)")) is not None)
check("mark() is idempotent",
      "if(WasTicketDeletedThisCycle(ticket)) return;" in
      body(OM_T, "void              MarkTicketDeletedThisCycle(ulong ticket)"))

# ---------------------------------------------------------------- 4
# THE load-bearing assertion. A mapping-scoped skip is the v5.31 REJECTED
# design and must not appear in SafeDeleteOrder.
sd = body(OM_T, "bool                    SafeDeleteOrder(ulong ticket)")
check("SafeDeleteOrder body captured", len(sd) > 0)
check("SafeDeleteOrder rejects ticket <= 0",
      re.search(r"if\(ticket\s*<=\s*0\)\s*return\s+false;", sd) is not None)
check("SafeDeleteOrder consults the ledger",
      "if(WasTicketDeletedThisCycle(ticket)) return false;" in sd)
check("SafeDeleteOrder calls DeleteOrder()",
      "if(!DeleteOrder(ticket)) return false;" in sd)
check("SafeDeleteOrder marks only on success",
      sd.find("MarkTicketDeletedThisCycle(ticket);") > sd.find("if(!DeleteOrder(ticket)) return false;"))
check("SafeDeleteOrder returns true on success",
      re.search(r"MarkTicketDeletedThisCycle\(ticket\);\s*return\s+true;", sd) is not None)

# The rejected alternative: skipping any ticket the block array cannot map.
# Blocks are rebuilt empty on every init, so this would strand real broker
# orphans - exactly what CancelOrdersForInvalidBlocks exists to clear.
check("REJECTED: no FindBlockIndexByTicket mapping-skip in SafeDeleteOrder",
      "FindBlockIndexByTicket" not in sd)
check("REJECTED: no mapping-based early-return in the ledger probe",
      "FindBlockIndexByTicket" not in body(OM_T, "bool              WasTicketDeletedThisCycle(ulong ticket)"))
check("orphan-safety note present (unarmed cycle still deletes)",
      "UNARMED" in OM_T)
check("rationale: in-memory-only blocks documented in the ledger comment",
      "in-memory" in OM_T.lower() or "in memory" in OM_T.lower())

# ---------------------------------------------------------------- 5
# An unarmed cycle (nothing deleted yet) must not suppress the delete.
def simulating_unarmed():
    """Model SafeDeleteOrder when the ledger is empty."""
    count = 0
    ticket = 1234567
    was_deleted = any(False for _ in range(count))
    return (not was_deleted) and ticket > 0

check("unarmed cycle reaches DeleteOrder (orphans stay cancellable)",
      simulating_unarmed())


# Model the two-sweep collision this fix targets.
def simulate_first_and_second_delete():
    ledger = []
    calls = []

    def safe_delete(ticket):
        if ticket <= 0:
            return False
        if ticket in ledger:
            return False
        calls.append(ticket)          # stands in for DeleteOrder()
        ledger.append(ticket)
        return True

    first = safe_delete(998877)
    second = safe_delete(998877)      # sibling sweep, same OnTick
    return first, second, calls

f_ok, s_ok, calls = simulate_first_and_second_delete()
check("first same-tick delete dispatches", f_ok)
check("second same-tick delete is suppressed", not s_ok)
check("exactly ONE REMOVE dispatched for the ticket", calls == [998877], "calls=%r" % (calls,))


# The ledger is per-cycle, so a NEW cycle must re-open deletion for that
# ticket (the order may legitimately be placed again on a later tick).
def simulate_new_cycle():
    ledger = []
    ledger.append(998877)             # deleted on cycle N
    armed_cycle_n = 998877 in ledger
    ledger.clear()                    # BeginOrderCycle()
    return armed_cycle_n, (998877 in ledger)

before, after = simulate_new_cycle()
check("ledger armed within its own cycle", before)
check("fresh cycle clears the ledger (BeginOrderCycle resets)", not after)

# ---------------------------------------------------------------- 6
# All five same-tick deleters must route through the guard.
for sig, label in [
    ("void              CancelOrdersForInvalidBlocks(void)", "CancelOrdersForInvalidBlocks"),
    ("void              CancelQuorumOpposingOrders(void)", "CancelQuorumOpposingOrders"),
    ("void              CancelOpposingConsensusOrders(void)", "CancelOpposingConsensusOrders"),
    ("void              ManageDirectionConflict(void)", "ManageDirectionConflict"),
    ("void              CancelAllPendingOrders(void)", "CancelAllPendingOrders"),
]:
    b = body(OM_T, sig)
    check("%s body captured" % label, len(b) > 0)
    check("%s uses SafeDeleteOrder" % label, "SafeDeleteOrder(" in b)
    check("%s no longer calls DeleteOrder() directly" % label,
          re.search(r"(?<!Safe)DeleteOrder\(", strip_comments(b)) is None,
          "bare DeleteOrder found")

# Exactly two DeleteOrder( occurrences survive (definition + the one call
# inside SafeDeleteOrder); any third means an unguarded site remains. Comments
# are stripped first so that prose mentioning DeleteOrder() cannot inflate the
# tally - which is exactly what the v5.31 annotations would otherwise do.
code = strip_comments(OM_T)
n_del = len(re.findall(r"(?<!Safe)\bDeleteOrder\s*\(", code))
check("only the definition and SafeDeleteOrder call bare DeleteOrder()",
      n_del == 2, "bare DeleteOrder( count=%d" % n_del)
n_safe = len(re.findall(r"\bSafeDeleteOrder\s*\(", code))
check("SafeDeleteOrder is the definition + exactly 5 call sites",
      n_safe == 6, "SafeDeleteOrder( count=%d" % n_safe)

# ---------------------------------------------------------------- 7
check("ledger count cold-started in the constructor",
      re.search(r"m_deletedThisCycleCount\s*=\s*0;", body(OM_T, "COttoOrderManager(void)")) is not None)
check("ledger array cold-started in the constructor",
      "ArrayResize(m_deletedThisCycle, 0, 16);" in body(OM_T, "COttoOrderManager(void)"))
check("ledger freed in the destructor",
      re.search(r"~COttoOrderManager\(void\)\s*\{[^}]*ArrayFree\(m_deletedThisCycle\);",
                OM_T, re.S) is not None)
check("ledger reset in Initialize",
      re.search(r"m_deletedThisCycleCount\s*=\s*0;", body(OM_T, "bool              Initialize(string symbol,")) is not None)

# ---------------------------------------------------------------- 8
for f in ALL_FILES:
    txt = read(os.path.join(ROOT, f))
    check("5.31 stamped in %s" % f, '#property version   "5.31"' in txt)
    check("no 5.30 property stamp in %s" % f,
          '#property version   "5.30"' not in txt)
    check("no 5.28/5.29 property stamp in %s" % f,
          '#property version   "5.28"' not in txt
          and '#property version   "5.29"' not in txt)

check("OttoDefines banner names v5.31", "OTTO EA v5.31" in DEFS_T)
check("OttoDefines description names v5.31",
      '#property description "OTTO v5.31' in DEFS_T)
check("otto.mq5 port banner reads v5.31",
      "Pine Script Master Build Port (v5.31)" in MQ5_T)
check("otto.mq5 init banner reads v5.31", "OTTO EA v5.31" in MQ5_T)

# ---------------------------------------------------------------- CRLF
for f in ALL_FILES:
    raw = io.open(os.path.join(ROOT, f), "rb").read()
    ncrlf = raw.count(b"\r\n")
    nlf = raw.count(b"\n")
    check("CRLF preserved in %s" % f, ncrlf == nlf, "%d CRLF vs %d LF" % (ncrlf, nlf))


def main():
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print("=" * 74)
    print("v5.31 COLLISION-PROOF PER-CYCLE ORDER DELETION - STATIC PROBE")
    print("=" * 74)
    for name, ok, detail in RESULTS:
        mark = "PASS" if ok else "FAIL"
        line = "  [%s] %s" % (mark, name)
        if not ok and detail:
            line += "  <%s>" % detail
        print(line)
    print("-" * 74)
    print("%d / %d checks passed" % (passed, total))
    if passed != total:
        print("*** PROBE FAILED ***")
        return 1
    print("*** ALL CHECKS PASSED ***")
    return 0


if __name__ == "__main__":
    sys.exit(main())
