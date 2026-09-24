"""v5.32 patch: OttoDefines.mqh -- roll back strict engines + add veto code.

Applied through io.open(..., encoding="utf-8", newline="") so the strict CRLF
line endings and the 3,361 non-ASCII (em-dash) bytes are preserved byte-for
byte. Every anchor is asserted to occur exactly once.
"""

import io
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "OttoDefines.mqh")

EMDASH = "\u2014"

# ---------------------------------------------------------------- enum member
NL = "\r\n"

OLD_ENUM = NL.join([
    "   VETO_CORRELATION         // Opposes the 8-currency vector consensus",
    "  };",
])
NEW_ENUM = NL.join([
    "   VETO_CORRELATION,        // Opposes the 8-currency vector consensus",
    "   // v5.32: MT5 pending-price boundary guard. Set when a block's entry",
    "   // price has drifted to the WRONG side of the live spread (a BUY LIMIT",
    "   // at/above the Ask, or a SELL LIMIT at/below the Bid), where MT5 would",
    "   // answer OrderSend() with TRADE_RETCODE_INVALID_PRICE (10015). Unlike",
    "   // the geographic vetoes above this one is not a market judgement -- it",
    "   // is a protocol precondition. The block is latched dead rather than",
    "   // retried every tick; see COttoOrderManager::PlaceLimitOrder.",
    "   VETO_PRICE_INVALID       // Entry past live market (limit would reject)",
    "  };",
])

# ------------------------------------------------------- [9] group rollback note
OLD_NOTE9 = (
    "// FIX (v5.26): portfolio-wide consensus engine ported from the theoretical"
)
NEW_NOTE9 = NL.join([
    "// v5.32 ROLLBACK: the three strict engines in this group remain compiled",
    "// and user-enableable, but all three now DEFAULT OFF. On live feeds the",
    "// 8-currency vector consensus, the opposing-pending cancellation and the",
    "// 4-pair quorum guard repeatedly suppressed valid setups, so the shipped",
    "// default is once again the loose classic model: open-position correlation",
    "// vetoes only (COttoCorrelationFilter::IsTradeVetoed ->",
    "// ScanPositionsForVeto) plus the HiveMind weighted-bias tie-breaker, which",
    "// was never gated by these inputs. Flip a field back on to re-arm it.",
    "// FIX (v5.26): portfolio-wide consensus engine ported from the theoretical",
])

# ------------------------------------------------------ three default flips
FLIPS = [
    ("input bool     InpEnableVectorEngine = true;   // Enable currency-vector consensus layer",
     "input bool     InpEnableVectorEngine = false;  // Enable currency-vector consensus layer"),
    ("input bool     InpCancelOpposingPendings = true; // Cancel resting pendings against consensus",
     "input bool     InpCancelOpposingPendings = false; // Cancel resting pendings against consensus"),
    ("input bool     InpEnableQuorumGuard      = true;   // Master toggle for 4-pair quorum",
     "input bool     InpEnableQuorumGuard      = false;  // Master toggle for 4-pair quorum"),
]

# ------------------------------------------------------- [10] group rollback note
OLD_NOTE10 = "// v5.27: LIVE 4-PAIR QUORUM CONTRADICTION GUARD."
NEW_NOTE10 = NL.join([
    "// v5.32 ROLLBACK: default OFF (see the [9] group header note).",
    "// v5.27: LIVE 4-PAIR QUORUM CONTRADICTION GUARD.",
])


def sub(text, old, new, label):
    n = text.count(old)
    if n != 1:
        raise SystemExit("ANCHOR ERROR [%s]: found %d occurrence(s), need 1" % (label, n))
    print("  ok  %s" % label)
    return text.replace(old, new, 1)


def main():
    text = io.open(PATH, encoding="utf-8", newline="").read()
    original = text

    text = sub(text, OLD_ENUM, NEW_ENUM, "ENUM_VETO_REASON += VETO_PRICE_INVALID")
    text = sub(text, OLD_NOTE9, NEW_NOTE9, "[9] rollback note")
    text = sub(text, OLD_NOTE10, NEW_NOTE10, "[10] rollback note")
    for i, (old, new) in enumerate(FLIPS, 1):
        text = sub(text, old, new, "default flip %d/3" % i)

    if EMDASH not in text:
        raise SystemExit("SANITY ERROR: em-dashes vanished from the file")
    if text == original:
        raise SystemExit("SANITY ERROR: nothing changed")
    if "true;" in NEW_NOTE9:
        raise SystemExit("SANITY ERROR: rollback note leaked a toggle")

    io.open(PATH, "w", encoding="utf-8", newline="").write(text)
    print("WROTE %s" % PATH)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
