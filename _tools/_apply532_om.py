"""v5.32 patch: COttoOrderManager.mqh -- MT5-exact pending-price boundary guard.

Applied through io.open(..., encoding="utf-8", newline="") so the strict CRLF
line endings and non-ASCII bytes survive byte-for-byte.
"""

import io
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "COttoOrderManager.mqh")

# ---------------------------------------------------------------------------
# 1. The guard itself, inserted immediately after the entry price is computed
#    and BEFORE the duplicate shield (so an invalid price never claims the
#    "already live" bookkeeping) and before hasPlacedOrder=true at the bottom
#    of the function (so a rejected send is not latched as placed).
# ---------------------------------------------------------------------------
ANCHOR = (
    "      double entryPrice = NormalizeDouble(CalcEntryPrice(block), _Digits);\n"
    "\n"
    "      // HARD ANTI-DUPLICATE CHECK against MT5's live pending-order book."
)

GUARD = """      double entryPrice = NormalizeDouble(CalcEntryPrice(block), _Digits);

      // v5.32 \u2014 MT5-EXACT PENDING-PRICE BOUNDARY GUARD
      // The server refuses a pending order whose price is on the wrong side of
      // the spread: a BUY LIMIT must rest strictly BELOW the Ask and a SELL
      // LIMIT strictly ABOVE the Bid. If the market gaps through the zone
      // between arming and placement, OrderSend() answers with
      // TRADE_RETCODE_INVALID_PRICE (10015) and the journal logs
      // "[Invalid price]" -- the retry ladder then burns for nothing.
      // Validate locally on the SAME side of the spread the server uses,
      // widened by the broker's stops/freeze buffer.
      double minPendDist = MathMax((double)SymbolInfoInteger(m_symbol, SYMBOL_TRADE_STOPS_LEVEL),
                                   (double)SymbolInfoInteger(m_symbol, SYMBOL_TRADE_FREEZE_LEVEL)) *
                           SymbolInfoDouble(m_symbol, SYMBOL_POINT);
      double liveAsk = GetAsk();
      double liveBid = GetBid();

      if(block.type == BLOCK_SUPPORT)
        {
         // BUY LIMIT: valid iff entry < Ask. Clamp the entry down onto the
         // legal boundary rather than discarding an otherwise sound zone.
         if(entryPrice >= liveAsk - minPendDist)
           {
            double clamped = NormalizeDouble(liveAsk - minPendDist, _Digits);
            if(clamped > 0)
               entryPrice = clamped;
            else
              {
               block.isVetoed   = true;
               block.vetoReason = VETO_PRICE_INVALID;
               m_blockManager.SetBlockAt(blockIndex, block);
               if(EnableLogging)
                  Print("[OrderManager] PRICE GUARD: BUY LIMIT ",
                        DoubleToString(entryPrice, _Digits), " >= Ask ",
                        DoubleToString(liveAsk, _Digits), " on ", m_symbol,
                        " \u2014 block latched dead (unclampable)");
               return false;
              }
           }
        }
      else
        {
         // SELL LIMIT: valid iff entry > Bid.
         if(entryPrice <= liveBid + minPendDist)
           {
            double clamped = NormalizeDouble(liveBid + minPendDist, _Digits);
            if(clamped > 0)
               entryPrice = clamped;
            else
              {
               block.isVetoed   = true;
               block.vetoReason = VETO_PRICE_INVALID;
               m_blockManager.SetBlockAt(blockIndex, block);
               if(EnableLogging)
                  Print("[OrderManager] PRICE GUARD: SELL LIMIT ",
                        DoubleToString(entryPrice, _Digits), " <= Bid ",
                        DoubleToString(liveBid, _Digits), " on ", m_symbol,
                        " \u2014 block latched dead (unclampable)");
               return false;
              }
           }
        }

      // HARD ANTI-DUPLICATE CHECK against MT5's live pending-order book."""

# ---------------------------------------------------------------------------
# 2. Journal label for the new veto so CancelOrdersForInvalidBlocks does not
#    mislabel a price-guard kill as "Manual".
# ---------------------------------------------------------------------------
OLD_LABEL = (
    '                   else if(blocks[i].vetoReason == VETO_CORRELATION) cancelReason = "Vector Consensus Veto";'
)
NEW_LABEL = (
    '                   else if(blocks[i].vetoReason == VETO_CORRELATION) cancelReason = "Vector Consensus Veto";\n'
    '                   else if(blocks[i].vetoReason == VETO_PRICE_INVALID) cancelReason = "Limit Price Past Market";'
)


def sub(text, old, new, label):
    n = text.count(old)
    if n != 1:
        raise SystemExit("ANCHOR ERROR [%s]: found %d occurrence(s), need 1" % (label, n))
    print("  ok  %s" % label)
    return text.replace(old, new, 1)


def crlf(s):
    """The target file is strict CRLF; the literals above are authored with LF."""
    return s.replace("\r\n", "\n").replace("\n", "\r\n")


def sub_crlf(text, old, new, label):
    return sub(text, crlf(old), crlf(new), label)


def main():
    text = io.open(PATH, encoding="utf-8", newline="").read()
    original = text

    text = sub_crlf(text, ANCHOR, GUARD, "price guard inserted after entry-price calc")
    text = sub_crlf(text, OLD_LABEL, NEW_LABEL, "VETO_PRICE_INVALID journal label")

    # brace sanity: the guard adds 4 x {} extra beyond the one block it opens
    # and closes; net open/close counts must stay balanced.
    if text.count("{") != text.count("}"):
        raise SystemExit("BRACE ERROR: %d { vs %d }" % (text.count("{"), text.count("}")))
    if text.count("VETO_PRICE_INVALID") != 3:
        raise SystemExit("SANITY ERROR: expected 3 VETO_PRICE_INVALID sites")
    if text == original:
        raise SystemExit("SANITY ERROR: nothing changed")

    io.open(PATH, "w", encoding="utf-8", newline="").write(text)
    print("WROTE %s" % PATH)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
