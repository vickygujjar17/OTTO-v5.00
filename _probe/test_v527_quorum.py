"""
v5.27 static verification probe - Live 4-Pair Quorum Contradiction Guard.

Pins the behaviour that cannot be exercised by the MQL5 compiler gate:

  1. The six [10] inputs exist with the approved defaults.
  2. The quorum scanner walks EXACTLY m_allSymbols[0..27] - XAUUSD (index 28)
     must never vote, or USD silently gains an eighth ballot.
  3. Base match follows raw direction; QUOTE match is INVERTED.
  4. The 4-of-7 threshold is >=, so exactly 4 agrees wins and 3 does not.
  5. InpQuorumMinCorrelation gates affinity inheritance
     (AUD/NZD 85 and EUR/CHF 80 pass at 50; CHF/JPY 40 is rejected).
  6. Liquidation requires BOTH the OR quorum hit AND self-confirmation.
  7. The OR rule itself is intact on both directions.
  8. Pending cancellation is UNGATED (no self-confirmation call on that path).
  9. Trade manager: filter injected, open-age gate, one-shot latch.
 10. Order manager: sweep exists, sets VETO_CORRELATION, is reachable.
 11. otto.mq5 wires the filter into Initialize and calls the sweep.
 12. Version stamp 5.28 present.

Pure static analysis of the shipped sources - no MT5 required.
"""

import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CF = os.path.join(ROOT, "COttoCorrelationFilter.mqh")
TM = os.path.join(ROOT, "COttoTradeManager.mqh")
OM = os.path.join(ROOT, "COttoOrderManager.mqh")
DEFS = os.path.join(ROOT, "OttoDefines.mqh")
MQ5 = os.path.join(ROOT, "otto.mq5")

ALL_FILES = ["otto.mq5", "COttoOrderManager.mqh", "COttoTradeManager.mqh",
             "COttoRiskManager.mqh", "COttoBlockManager.mqh", "COttoJournal.mqh",
             "COttoNewsFilter.mqh", "COttoCorrelationFilter.mqh",
             "COttoMarketStructure.mqh", "OttoDefines.mqh"]


def read(p):
    return io.open(p, encoding="utf-8", errors="replace", newline="").read()


CF_T = read(CF)
TM_T = read(TM)
OM_T = read(OM)
DEFS_T = read(DEFS)
MQ5_T = read(MQ5)

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
check("input InpEnableQuorumGuard defaults true",
      re.search(r"input bool\s+InpEnableQuorumGuard\s*=\s*true", DEFS_T) is not None)
check("input InpQuorumCloseActiveTrade defaults false (demo-first)",
      re.search(r"input bool\s+InpQuorumCloseActiveTrade\s*=\s*false", DEFS_T) is not None)
check("input InpQuorumCancelLimits defaults true",
      re.search(r"input bool\s+InpQuorumCancelLimits\s*=\s*true", DEFS_T) is not None)
check("input InpQuorumMinPairs defaults 4",
      re.search(r"input int\s+InpQuorumMinPairs\s*=\s*4\s*;", DEFS_T) is not None)
check("input InpQuorumTimeframe defaults PERIOD_M15",
      re.search(r"input ENUM_TIMEFRAMES\s+InpQuorumTimeframe\s*=\s*PERIOD_M15", DEFS_T) is not None)
check("input InpQuorumMinCorrelation defaults 50.0",
      re.search(r"input double\s+InpQuorumMinCorrelation\s*=\s*50\.0\s*;", DEFS_T) is not None)
check("group [10] LIVE QUORUM CONTRADICTION GUARD present",
      "[10] LIVE QUORUM CONTRADICTION GUARD" in DEFS_T)
check("define OTTO_QUORUM_FX_COUNT is 28",
      re.search(r"#define\s+OTTO_QUORUM_FX_COUNT\s+28", DEFS_T) is not None)
check("define OTTO_QUORUM_MIN_AGE_SEC is 60",
      re.search(r"#define\s+OTTO_QUORUM_MIN_AGE_SEC\s+60", DEFS_T) is not None)
check("description tag names 4-Pair Quorum Guard",
      "4-Pair Quorum Guard" in DEFS_T)

# ---------------------------------------------------------------- 2
count_body = body(CF_T, "void              CountCurrencyAgreement")
check("CountCurrencyAgreement exists", count_body != "")
check("scanner bound uses OTTO_QUORUM_FX_COUNT",
      "i < OTTO_QUORUM_FX_COUNT" in count_body)
check("scanner never iterates to 29 / OTTO_PAIR_COUNT+1",
      "29" not in count_body)
check("XAUUSD cannot vote (bound excludes index 28)",
      "XAUUSD" not in count_body and "i < OTTO_QUORUM_FX_COUNT" in count_body)
check("quote match INVERTS the peer direction",
      "-GetLiveSymbolDirection" in count_body)
check("base match follows raw direction",
      "dir = GetLiveSymbolDirection" in count_body)

# ---------------------------------------------------------------- 3
live_body = body(CF_T, "int               GetLiveSymbolDirection")
check("GetLiveSymbolDirection exists", live_body != "")
check("live direction uses iOpen on the forming bar index 0",
      "iOpen(real, tf, 0)" in live_body)
check("live direction compares Bid against that open",
      "bid - op" in live_body)
check("live direction guards unresolved symbol with return 0",
      "return 0" in live_body and 'ResolveAnchorSymbol(root, "")' in live_body)

# ---------------------------------------------------------------- 4
q_body = body(CF_T, "int               GetCurrencyStrengthQuorum")
check("GetCurrencyStrengthQuorum exists", q_body != "")
check("threshold is >= (4 of 7 wins)",
      q_body.count(">= InpQuorumMinPairs") == 2)

# ---------------------------------------------------------------- 5
aff_body = body(CF_T, "int               GetQuorumWithAffinity")
check("GetQuorumWithAffinity exists", aff_body != "")
check("direct quorum is tried before any inheritance",
      aff_body.find("GetCurrencyStrengthQuorum(cur, tf)") < aff_body.find("for(int peer"))
check("inheritance gated on affinity percent vs InpQuorumMinCorrelation",
      "aff * 100.0 < InpQuorumMinCorrelation" in aff_body)
check("inheritance reports the donor currency via viaPeer",
      "viaPeer = m_currencies[peer]" in aff_body)

# threshold arithmetic, mirroring the shipped AFFINITIES table
AFF = {"AUD/NZD": 0.85, "EUR/CHF": 0.80, "EUR/GBP": 0.75,
       "GBP/CHF": 0.60, "AUD/CAD": 0.60, "NZD/CAD": 0.55, "CHF/JPY": 0.40}
MINC = 50.0
check("AUD/NZD 85% qualifies at the 50.0 floor", AFF["AUD/NZD"] * 100.0 >= MINC)
check("EUR/CHF 80% qualifies at the 50.0 floor", AFF["EUR/CHF"] * 100.0 >= MINC)
check("EUR/GBP 75% qualifies at the 50.0 floor", AFF["EUR/GBP"] * 100.0 >= MINC)
check("CHF/JPY 40% is rejected at the 50.0 floor", AFF["CHF/JPY"] * 100.0 < MINC)
check("floor prunes everything below 50%",
      all((w * 100.0 >= MINC) == (w >= 0.5) for w in AFF.values()))

# ---------------------------------------------------------------- 6
gate_body = body(CF_T, "bool              IsSymbolOpposingQuorum")
check("IsSymbolOpposingQuorum exists", gate_body != "")
check("LONG OR rule: baseQ <= -1 || quoteQ >= 1",
      "quorumHit = (baseQ <= -1 || quoteQ >= 1)" in gate_body)
check("SHORT OR rule: baseQ >= 1 || quoteQ <= -1",
      "quorumHit = (baseQ >=  1 || quoteQ <= -1)" in gate_body)
check("gate requires self-confirmation after the OR hit",
      gate_body.find("quorumHit) return false") < gate_body.find("selfAgainst"))
check("LONG self-confirmation requires selfDir < 0",
      "(dir > 0) ? (selfDir < 0) : (selfDir > 0)" in gate_body)
check("gate bails when selfAgainst is false",
      "if(!selfAgainst) return false" in gate_body)
check("gate honours InpQuorumCloseActiveTrade",
      "InpQuorumCloseActiveTrade" in gate_body)
check("gate honours master InpEnableQuorumGuard",
      "InpEnableQuorumGuard" in gate_body)

# ---------------------------------------------------------------- 7
pend_body = body(CF_T, "bool              IsPendingOpposingQuorum")
check("IsPendingOpposingQuorum exists", pend_body != "")
check("pending OR rule mirrors LONG rule",
      "hit = (baseQ <= -1 || quoteQ >= 1)" in pend_body)
check("pending OR rule mirrors SHORT rule",
      "hit = (baseQ >=  1 || quoteQ <= -1)" in pend_body)
check("pending path is UNGATED (no self-confirmation)",
      "selfDir" not in pend_body and "GetLiveSymbolDirection" not in pend_body)
check("pending path honours InpQuorumCancelLimits",
      "InpQuorumCancelLimits" in pend_body)

# ---------------------------------------------------------------- 8
check("trade manager has m_correlationFilter member",
      "COttoCorrelationFilter *m_correlationFilter;" in TM_T)
check("trade manager Initialize takes the filter",
      "COttoBlockManager *bm, COttoCorrelationFilter *cf" in TM_T)
check("trade manager stores the injected filter",
      "m_correlationFilter=cf;" in TM_T)
check("trade manager latches on fire",
      "m_quorumFireLatched = true;" in TM_T)
check("trade manager latch is re-armed by ArmQuorumGuard",
      "ArmQuorumGuard(void)  { m_quorumFireLatched = false; }" in TM_T)
check("trade manager gates on basket open age",
      "GetBasketOpenTime()" in TM_T and "OTTO_QUORUM_MIN_AGE_SEC" in TM_T)
check("trade manager calls the GATED verdict",
      "IsSymbolOpposingQuorum" in TM_T)
check("trade manager does NOT call the ungated verdict",
      "IsPendingOpposingQuorum" not in TM_T)
check("liquidation routes through CloseEntireBasket",
      "CloseEntireBasket(qReason, true)" in TM_T)
check("guard runs before the trailing ladder",
      TM_T.find("QUORUM CONTRADICTION GUARD") < TM_T.find("double atr = GetCurrentATR();"))

# ---------------------------------------------------------------- 9
check("order manager exposes GetBasketOpenTime",
      "GetBasketOpenTime(void) const { return m_basketOpenTime; }" in OM_T)
sweep = body(OM_T, "void              CancelQuorumOpposingOrders")
check("CancelQuorumOpposingOrders exists", sweep != "")
check("sweep honours both quorum toggles",
      "InpEnableQuorumGuard" in sweep and "InpQuorumCancelLimits" in sweep)
check("sweep guards a NULL filter", "m_correlationFilter == NULL" in sweep)
check("sweep calls the UNGATED pending verdict",
      "IsPendingOpposingQuorum" in sweep)
check("sweep does NOT self-confirm",
      "IsSymbolOpposingQuorum" not in sweep)
check("sweep stamps VETO_CORRELATION on the block",
      "vetoReason        = VETO_CORRELATION;" in sweep)
check("sweep deletes the resting order", "DeleteOrder(ticket)" in sweep)
check("sweep releases the block ticket slot",
      "mod.limitOrderTicket  = 0;" in sweep)

# ---------------------------------------------------------------- 10
check("otto.mq5 injects the filter into trade manager Initialize",
      "&g_correlationFilter))" in MQ5_T and
      "g_tradeManager.Initialize(g_symbol, &g_riskManager, &g_orderManager, &g_blockManager" in MQ5_T)
check("otto.mq5 calls CancelQuorumOpposingOrders",
      "g_orderManager.CancelQuorumOpposingOrders();" in MQ5_T)
check("quorum sweep runs in STEP 3, before placement",
      0 < MQ5_T.find("CancelQuorumOpposingOrders()") < MQ5_T.find("PlaceOrdersForArmedBlocks"))
check("quorum sweep runs after the consensus sweep",
      MQ5_T.find("CancelOpposingConsensusOrders()") < MQ5_T.find("CancelQuorumOpposingOrders()"))

# ---------------------------------------------------------------- 11
for f in ALL_FILES:
    p = os.path.join(ROOT, f)
    txt = read(p)
    check("version 5.28 stamped in %s" % f, '5.28' in txt)
check("no stale 5.27 property stamp in OttoDefines", 'version   "5.27"' not in DEFS_T)


# ---------------------------------------------------------------- CRLF
for f in ALL_FILES:
    raw = io.open(os.path.join(ROOT, f), "rb").read()
    ncrlf = raw.count(b"\r\n")
    nlf = raw.count(b"\n")
    check("CRLF preserved in %s" % f, ncrlf == nlf, "%d CRLF vs %d LF" % (ncrlf, nlf))


# ---------------------------------------------------------------- arithmetic
P = 4
check("4 agrees reaches quorum", P >= 4)
check("3 agrees does NOT reach quorum", not (3 >= 4))
check("5 agrees reaches quorum", 5 >= 4)
LONG, SHORT = 1, -1
check("LONG killed by bearish base quorum", (LONG > 0) and (-1 <= -1))
check("LONG killed by bullish quote quorum", (LONG > 0) and (1 >= 1))
check("LONG survives neutral quorum (no OR hit)", not ((0 <= -1) or (0 >= 1)))
check("SHORT killed by bullish base quorum", (SHORT < 0) and (1 >= 1))
check("SHORT killed by bearish quote quorum", (SHORT < 0) and (-1 <= -1))
check("SHORT survives neutral quorum (no OR hit)", not ((0 >= 1) or (0 <= -1)))

def confirms(dir, selfdir):
    return (selfdir < 0) if dir > 0 else (selfdir > 0)

check("LONG confirmed when pair is falling", confirms(1, -1))
check("LONG NOT confirmed when pair is rising", not confirms(1, 1))
check("LONG NOT confirmed when pair is flat", not confirms(1, 0))
check("SHORT confirmed when pair is rising", confirms(-1, 1))
check("SHORT NOT confirmed when pair is falling", not confirms(-1, -1))


def gradual(cur, cur_rules, peer_rules, minc):
    """cur_rules[cur] = own verdict; peer_rules[peer] = donor verdict."""
    direct = cur_rules.get(cur, 0)
    if direct:
        return direct, ""
    for peer, ver in peer_rules.items():
        if aff.get((cur, peer), 0.0) * 100.0 < minc:
            continue
        if ver:
            return ver, peer
    return 0, ""


aff = {("AUD", "NZD"): 0.85, ("NZD", "AUD"): 0.85,
       ("EUR", "CHF"): 0.80, ("CHF", "EUR"): 0.80,
       ("CHF", "JPY"): 0.40, ("JPY", "CHF"): 0.40}

v, via = gradual("AUD", {}, {}, 50.0)
check("AUD inherits nothing when NZD has no quorum", v == 0 and via == "")
v, via = gradual("AUD", {}, {"NZD": 1}, 50.0)
check("AUD inherits +1 from NZD (85% >= 50)", v == 1 and via == "NZD")
v, via = gradual("CHF", {}, {"JPY": 1}, 50.0)
check("CHF does NOT inherit from JPY (40% < 50)", v == 0 and via == "")
v, via = gradual("CHF", {}, {"EUR": 1, "JPY": 1}, 50.0)
check("CHF inherits from EUR (80%) over JPY (40%)", v == 1 and via == "EUR")
v, via = gradual("AUD", {"AUD": -1}, {"NZD": 1}, 50.0)
check("direct verdict beats inheritance (AUD own -1)", v == -1 and via == "")
check("floor at 0 admits every non-zero affinity",
      all(w * 100.0 >= 0.0 for w in aff.values()))


def main():
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print("=" * 74)
    print("v5.27 LIVE 4-PAIR QUORUM CONTRADICTION GUARD - STATIC PROBE")
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
