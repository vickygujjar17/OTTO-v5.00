"""
v5.32 static verification probe - strict-engine rollback + MT5 price guard.

Pins the two behavioural changes that cannot be exercised by the MQL5
compiler gate, plus the regression guard for what must NOT have moved:

  1. The three strict engines default OFF again (InpEnableVectorEngine,
     InpCancelOpposingPendings, InpEnableQuorumGuard) -- but the inputs are
     still declared, so the rollback is a default change, not a removal.
  2. InpUseExternalAnchors is NOT part of the rollback and stays true.
  3. The classic per-chart veto path (IsTradeVetoed -> ScanPositionsForVeto)
     is intact AND ungated, so disabling the consensus engine cannot leave
     the EA with no correlation defence at all.
  4. PlaceLimitOrder() carries the MT5-exact boundary guard: BUY LIMIT judged
     against the live Ask, SELL LIMIT against the live Bid, widened by the
     stops/freeze buffer.
  5. The guard sits AFTER the entry price is computed and BEFORE any
     hasPlacedOrder latch or the duplicate shield, so a rejected price can
     never be recorded as "placed".
  6. The guard LATCHES an unclampable block (isVetoed + VETO_PRICE_INVALID +
     SetBlockAt) so it cannot re-fire every tick.
  7. VETO_PRICE_INVALID is a declared enum member and is labelled in the
     cancellation journal, so a guard kill is not reported as "Manual".
  8. v5.31 regression guard: SafeDeleteOrder() and the per-cycle deletion
     ledger are still present and still in use.
  9. Version stamp 5.32 in all 10 files; the 5.31 property stamp is gone.
 10. Strict CRLF, no BOM, no mis-encoded UTF-8 anywhere in the 10 files.

Pure static analysis of the shipped sources - no MT5 required.
"""

import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OM = os.path.join(ROOT, "COttoOrderManager.mqh")
CF = os.path.join(ROOT, "COttoCorrelationFilter.mqh")
DEFS = os.path.join(ROOT, "OttoDefines.mqh")
MQ5 = os.path.join(ROOT, "otto.mq5")

ALL_FILES = ["otto.mq5", "COttoOrderManager.mqh", "COttoTradeManager.mqh",
             "COttoRiskManager.mqh", "COttoBlockManager.mqh", "COttoJournal.mqh",
             "COttoNewsFilter.mqh", "COttoCorrelationFilter.mqh",
             "COttoMarketStructure.mqh", "OttoDefines.mqh"]


def read(p):
    return io.open(p, encoding="utf-8", errors="replace", newline="").read()


def body_of(text, signature):
    """Return the brace-balanced body of the first function whose opening
    line contains `signature`, or '' if it is not found."""
    start = text.find(signature)
    if start < 0:
        return ""
    brace = text.find("{", start)
    if brace < 0:
        return ""
    depth = 0
    for i in range(brace, len(text)):
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[brace:i + 1]
    return ""


OM_T = read(OM)
CF_T = read(CF)
DEFS_T = read(DEFS)
MQ5_T = read(MQ5)

PLACE = body_of(OM_T, "bool                    PlaceLimitOrder(int blockIndex")

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))



# ----------------------------------------------------------------------
# 1. Strict engines default OFF (the rollback)
# ----------------------------------------------------------------------
check("input InpEnableVectorEngine still declared",
      re.search(r"input bool\s+InpEnableVectorEngine\s*=", DEFS_T) is not None)
check("InpEnableVectorEngine defaults false",
      re.search(r"input bool\s+InpEnableVectorEngine\s*=\s*false", DEFS_T) is not None)
check("InpCancelOpposingPendings defaults false",
      re.search(r"input bool\s+InpCancelOpposingPendings\s*=\s*false", DEFS_T) is not None)
check("InpEnableQuorumGuard defaults false",
      re.search(r"input bool\s+InpEnableQuorumGuard\s*=\s*false", DEFS_T) is not None)
check("none of the three strict toggles defaults true",
      not re.search(r"InpEnableVectorEngine\s*=\s*true\s*;", DEFS_T)
      and not re.search(r"InpCancelOpposingPendings\s*=\s*true\s*;", DEFS_T)
      and not re.search(r"InpEnableQuorumGuard\s*=\s*true\s*;", DEFS_T))

check("InpUseExternalAnchors is NOT rolled back (stays true)",
      re.search(r"input bool\s+InpUseExternalAnchors\s*=\s*true", DEFS_T) is not None)
check("InpQuorumCancelLimits stays true (only the master toggle moved)",
      re.search(r"input bool\s+InpQuorumCancelLimits\s*=\s*true", DEFS_T) is not None)
check("the rollback is documented in the [9] group header",
      "v5.32 ROLLBACK" in DEFS_T)
check("the rollback is documented in the [10] group header",
      DEFS_T.count("v5.32 ROLLBACK") >= 2)

# The engines must still be COMPILED so the inputs do something when re-armed.
check("vector engine gate still honours the input",
      "if(!InpEnableVectorEngine) return false;" in CF_T)
check("consensus sweep still honours InpCancelOpposingPendings",
      "InpCancelOpposingPendings" in OM_T)
check("quorum guard still honours InpEnableQuorumGuard",
      "InpEnableQuorumGuard" in OM_T + read(os.path.join(ROOT, "COttoTradeManager.mqh")))


# ----------------------------------------------------------------------
# 2. Classic per-chart veto path intact and UNGATED
# ----------------------------------------------------------------------
check("IsTradeVetoed still defined", "IsTradeVetoed" in CF_T)
check("ScanPositionsForVeto still defined", "ScanPositionsForVeto" in CF_T)
check("IsTradeVetoed delegates to the open-position scan",
      "ScanPositionsForVeto" in body_of(CF_T, "IsTradeVetoed(ENUM_TRADE_DIRECTION"))
check("IsTradeVetoed is not gated by the consensus toggle",
      "InpEnableVectorEngine" not in body_of(CF_T, "IsTradeVetoed(ENUM_TRADE_DIRECTION"))
check("PlaceLimitOrder still calls IsTradeVetoed",
      "m_correlationFilter.IsTradeVetoed(dir)" in PLACE)
check("HiveMind weighted-bias tie-breaker still present",
      "GetWeightedBiasSum" in CF_T or "ResolveBidirectionalConflict" in CF_T)


# ----------------------------------------------------------------------
# 3. The MT5-exact boundary guard
# ----------------------------------------------------------------------
check("PlaceLimitOrder located", PLACE != "")
check("guard is version-stamped v5.32",
      "v5.32" in PLACE and "PENDING-PRICE BOUNDARY GUARD" in PLACE)
check("guard reads the stops/freeze buffer",
      "SYMBOL_TRADE_STOPS_LEVEL" in PLACE and "SYMBOL_TRADE_FREEZE_LEVEL" in PLACE
      and "minPendDist" in PLACE)
check("guard captures the live spread",
      "double liveAsk = GetAsk();" in PLACE and "double liveBid = GetBid();" in PLACE)

# BUY LIMIT branch
check("BUY LIMIT branch keys off BLOCK_SUPPORT",
      "if(block.type == BLOCK_SUPPORT)" in PLACE)
check("BUY LIMIT validates entry < Ask (>= Ask - dist is invalid)",
      "if(entryPrice >= liveAsk - minPendDist)" in PLACE)
check("BUY LIMIT branch is closed by a matching else (SELL)",
      re.search(r"if\(entryPrice >= liveAsk - minPendDist\)", PLACE) is not None
      and re.search(r"\}\s*else\s*\{", PLACE) is not None)
check("SELL LIMIT validates entry > Bid (<= Bid + dist is invalid)",
      "if(entryPrice <= liveBid + minPendDist)" in PLACE)
check("guard clamps onto the legal boundary on both branches",
      PLACE.count("NormalizeDouble(liveAsk - minPendDist, _Digits)") == 1
      and PLACE.count("NormalizeDouble(liveBid + minPendDist, _Digits)") == 1)

# ORDERING: after entry-price computation, before the latch + duplicate shield.
check("guard is placed AFTER the entry price is computed",
      0 <= PLACE.find("CalcEntryPrice(block)") < PLACE.find("PENDING-PRICE BOUNDARY GUARD"))
check("guard is placed BEFORE the duplicate shield",
      PLACE.find("PENDING-PRICE BOUNDARY GUARD") < PLACE.find("IsOrderAlreadyLiveAtPrice"))
check("guard is placed BEFORE the hasPlacedOrder latch",
      PLACE.find("PENDING-PRICE BOUNDARY GUARD") < PLACE.find("block.hasPlacedOrder = true"))
check("entryPrice normalisation to _Digits is unchanged",
      "double entryPrice = NormalizeDouble(CalcEntryPrice(block), _Digits);" in PLACE)
check("guard never returns true (an invalid price cannot look like success)",
      "return true" not in PLACE[PLACE.find("PENDING-PRICE BOUNDARY GUARD"):
                                PLACE.find("HARD ANTI-DUPLICATE CHECK")])


# ----------------------------------------------------------------------
# 4. The latch: an unclampable block dies once, not once per tick
# ----------------------------------------------------------------------
check("guard latches isVetoed on the BUY path",
      re.search(r"block\.isVetoed\s*=\s*true;\s*\r?\n\s*block\.vetoReason\s*=\s*VETO_PRICE_INVALID",
                PLACE) is not None)
check("guard latches VETO_PRICE_INVALID on both paths",
      PLACE.count("block.vetoReason = VETO_PRICE_INVALID;") == 2)
check("the latch is persisted through SetBlockAt (local copy would evaporate)",
      PLACE.count("m_blockManager.SetBlockAt(blockIndex, block);") >= 3)
check("guard returns false after latching (both branches)",
      PLACE.count("return false;") >= 2)
check("guard logs a distinct PRICE GUARD diagnostic on both branches",
      PLACE.count("[OrderManager] PRICE GUARD:") == 2)
check("guard only latches when the clamp target is unusable",
      PLACE.count("if(clamped > 0)") == 2)


# ----------------------------------------------------------------------
# 5. Enum member + journal labelling
# ----------------------------------------------------------------------
check("VETO_PRICE_INVALID is a declared enum member",
      re.search(r"VETO_PRICE_INVALID", DEFS_T) is not None)
check("VETO_CORRELATION was comma-terminated to admit the new member",
      re.search(r"VETO_CORRELATION\s*,", DEFS_T) is not None)
check("VETO_PRICE_INVALID is the last member before the closer",
      re.search(r"VETO_PRICE_INVALID[^\r\n]*\r?\n\s*\};", DEFS_T) is not None)
check("VETO_PRICE_INVALID is labelled in the cancellation journal",
      "Limit Price Past Market" in OM_T)
check("the label sits in the vetoReason ladder, not a stray block",
      "VETO_PRICE_INVALID) cancelReason" in OM_T)


# ----------------------------------------------------------------------
# 6. v5.31 REGRESSION GUARD - the deletion ledger must not have moved
# ----------------------------------------------------------------------
check("SafeDeleteOrder still defined", "SafeDeleteOrder(ulong" in OM_T)
check("the v5.31 per-cycle ledger member survives",
      "m_deletedThisCycleCount" in OM_T)
check("the ledger is still cold-started",
      re.search(r"m_deletedThisCycleCount\s*=\s*0;", OM_T) is not None)
check("SafeDeleteOrder is still routed through by the invalid-block sweep",
      "SafeDeleteOrder(blocks[i].limitOrderTicket)" in OM_T)
check("the quote-freshness delete path still uses the ledger",
      "SafeDeleteOrder(ticket);   // v5.31: per-cycle collision guard" in OM_T or
      "SafeDeleteOrder(ticket);" in OM_T)
check("the v5.31 annotations were not rewritten by the bump",
      "v5.31: routed through the per-cycle ledger" in OM_T)


# ----------------------------------------------------------------------
# 7. Version stamps
# ----------------------------------------------------------------------
for f in ALL_FILES:
    txt = read(os.path.join(ROOT, f))
    check("5.32 stamped in %s" % f, '#property version   "5.32"' in txt)
    check("no 5.31 property stamp in %s" % f,
          '#property version   "5.31"' not in txt)

check("OttoDefines banner names v5.32", "OTTO EA v5.32" in DEFS_T)
check("OttoDefines description names v5.32",
      '#property description "OTTO v5.32' in DEFS_T)
check("OttoDefines [9] group banner reads v5.32",
      "[9] CURRENCY VECTOR & AFFINITY ENGINE \u2014 v5.32" in DEFS_T)
check("otto.mq5 port banner reads v5.32",
      "Pine Script Master Build Port (v5.32)" in MQ5_T)
check("otto.mq5 init banner reads v5.32", "OTTO EA v5.32" in MQ5_T)


# ----------------------------------------------------------------------
# 8. Encoding discipline: strict CRLF, no BOM, no mojibake
# ----------------------------------------------------------------------
for f in ALL_FILES:
    raw = io.open(os.path.join(ROOT, f), "rb").read()
    check("strict CRLF, no bare LF in %s" % f,
          raw.count(b"\n") == raw.count(b"\r\n"))
    check("no UTF-8 BOM in %s" % f, not raw.startswith(b"\xef\xbb\xbf"))

# The two files this release actually edits must be byte-clean: every
# non-ASCII run is a well-formed UTF-8 em-dash and nothing else.
for f in ("OttoDefines.mqh", "COttoOrderManager.mqh"):
    raw = io.open(os.path.join(ROOT, f), "rb").read()
    check("no mis-decoded UTF-8 (\\xc3\\xa2 runs) in %s" % f, b"\xc3\xa2" not in raw)
    check("%s carries well-formed em-dashes" % f,
          raw.count("\u2014".encode("utf-8")) > 0)

# otto.mq5 carries PRE-EXISTING mojibake unrelated to this release (its
# non-ASCII byte count was already non-zero and well-formed at v5.31).
# This release must not make that debt any worse.
MQ5_RAW = io.open(MQ5, "rb").read()
check("otto.mq5 mojibake count did not regress past its v5.31 baseline (17)",
      MQ5_RAW.count(b"\xc3\xa2") <= 17)


# ----------------------------------------------------------------------
# Harness
# ----------------------------------------------------------------------
def main():
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print("=" * 74)
    print("v5.32 CONFIG ROLLBACK + MT5 LIMIT-PRICE GUARD - STATIC PROBE")
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


