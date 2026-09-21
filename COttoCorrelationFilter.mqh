//+------------------------------------------------------------------+
//|                                        COttoCorrelationFilter.mqh |
//|              MODULE — Weighted Correlation Matrix (-3 to +3)      |
//|              28-Pair + Gold Institutional portfolio filter        |
//|              Suffix-safe (handles broker suffixes like .x)        |
//+------------------------------------------------------------------+
#property copyright "OTTO EA - Goat Funded Trader (GFT) Master Build"
#property version   "5.15"

#ifndef __OTTO_CORRELATION_FILTER__
#define __OTTO_CORRELATION_FILTER__

#include "OttoDefines.mqh"

//+------------------------------------------------------------------+
//| COttoCorrelationFilter class                                     |
//| Component-decomposition correlation engine for 28 FX pairs + gold |
//| Suffix-safe: broker suffixes (.x, m, _i, etc.) are sanitized.     |
//| Veto threshold: |score| >= 2.                                    |
//| Hive Mind tie-breaker: Total >= 2 or <= -2, else delete both.    |
//+------------------------------------------------------------------+
class COttoCorrelationFilter
  {
private:
   string            m_symbol;
   string            m_allSymbols[];

   //+------------------------------------------------------------------+
   //| CleanSymbol — strip broker suffix/prefix, return 6-char FX root  |
   //| Gold is normalized to "XAUUSD" so XAUUSD.x / GOLD.x all agree.    |
   //| NOTE: extracts A-Z only; intended for the FX + gold universe.     |
   //+------------------------------------------------------------------+
   string            CleanSymbol(string sym)
     {
      StringToUpper(sym);
      if(StringFind(sym, "XAUUSD") >= 0 || StringFind(sym, "GOLD") >= 0) return "XAUUSD";

      string base = "";
      int len = StringLen(sym);
      for(int i = 0; i < len; i++)
        {
         ushort ch = StringGetCharacter(sym, i);
         if(ch >= 'A' && ch <= 'Z') base += ShortToString(ch);
         if(StringLen(base) == 6) break;
        }
      return base;
     }

   //+------------------------------------------------------------------+
   //| MODULE 1: GetCorrelationScore — dynamic Base/Quote decomposition |
   //| Score is built from currency exposure, not a hardcoded ladder.    |
   //|   +2 shared base / shared quote   (same-term, moves together)     |
   //|   -2 inverted base/quote          (opposite-term, moves against)  |
   //|   +1 macro regional bloc, applied ONLY when no direct exposure    |
   //| Returns 0 when the two symbols sanitize to the same root.         |
   //+------------------------------------------------------------------+
   int               GetCorrelationScore(string sym1, string sym2)
     {
      string s1 = CleanSymbol(sym1);
      string s2 = CleanSymbol(sym2);
      if(s1 == s2) return 0;

      // Handle Gold exception
      if(s1 == "XAUUSD" || s2 == "XAUUSD")
        {
         string other = (s1 == "XAUUSD") ? s2 : s1;
         if(StringFind(other, "USD") == 3) return 2;  // e.g. EURUSD
         if(StringFind(other, "USD") == 0) return -2; // e.g. USDJPY
         return 0;
        }

      string base1  = StringSubstr(s1, 0, 3);
      string quote1 = StringSubstr(s1, 3, 3);
      string base2  = StringSubstr(s2, 0, 3);
      string quote2 = StringSubstr(s2, 3, 3);

      int score = 0;

      // 1. Direct Exposure Engine
      if(base1 == base2)   score += 2;
      if(quote1 == quote2) score += 2;
      if(base1 == quote2)  score -= 2;
      if(quote1 == base2)  score -= 2;

      // 2. Macro Regional Bloc Engine (Only applies if no direct exposure exists)
      if(score == 0)
        {
         // Commodity Bloc: AUD, NZD, CAD
         bool isComm1 = (base1=="AUD" || base1=="NZD" || base1=="CAD" || quote1=="AUD" || quote1=="NZD" || quote1=="CAD");
         bool isComm2 = (base2=="AUD" || base2=="NZD" || base2=="CAD" || quote2=="AUD" || quote2=="NZD" || quote2=="CAD");
         if(isComm1 && isComm2) score += 1;

         // European Bloc: EUR, GBP, CHF
         bool isEuro1 = (base1=="EUR" || base1=="GBP" || base1=="CHF" || quote1=="EUR" || quote1=="GBP" || quote1=="CHF");
         bool isEuro2 = (base2=="EUR" || base2=="GBP" || base2=="CHF" || quote2=="EUR" || quote2=="GBP" || quote2=="CHF");
         if(isEuro1 && isEuro2) score += 1;
        }

      return score;
     }

   //+------------------------------------------------------------------+
   //| Scans all open positions for correlation conflicts               |
   //+------------------------------------------------------------------+
   bool              ScanPositionsForVeto(int proposedDir)
     {
      for(int i = PositionsTotal() - 1; i >= 0; i--)
        {
         ulong ticket = PositionGetTicket(i);
         if(ticket <= 0) continue;
         if(!PositionSelectByTicket(ticket)) continue;
         string posSymbol = PositionGetString(POSITION_SYMBOL);
         long   posType   = PositionGetInteger(POSITION_TYPE);

         // FIX (v5.15): manual GOLD positions are factored into portfolio
         // correlation. Scoped narrowly: (1) only gold, and (2) only TRULY
         // manual tickets (magic == 0) - other EAs positions are left alone.
         // Note gold scores +/-2 against every USD pair, i.e. exactly the
         // veto threshold, so this deliberately widens the veto surface to
         // external gold. It is NOT applied to FX pairs.
         bool isExternalGold = (CleanSymbol(posSymbol) == "XAUUSD" &&
                                PositionGetInteger(POSITION_MAGIC) == 0);
         if(!isExternalGold && PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;
         int    posDir    = (posType == POSITION_TYPE_BUY) ? 1 : -1;
         int    score     = GetCorrelationScore(m_symbol, posSymbol);

         // |score| >= 2 is strong correlation — apply veto
         if(proposedDir == 1)  // Proposed LONG
           {
            if(posDir == 1 && score <= -2) return true;
            if(posDir == -1 && score >= 2) return true;
           }
         if(proposedDir == -1) // Proposed SHORT
           {
            if(posDir == -1 && score <= -2) return true;
            if(posDir == 1 && score >= 2) return true;
           }
        }
      return false;
     }


public:
   //+------------------------------------------------------------------+
   //| Constructor — populate the 28-pair + gold symbol universe        |
   //+------------------------------------------------------------------+
                     COttoCorrelationFilter(void)
     {
      m_symbol = "";
      ArrayResize(m_allSymbols, 29);
      m_allSymbols[0]  = "EURUSD";
      m_allSymbols[1]  = "GBPUSD";
      m_allSymbols[2]  = "AUDUSD";
      m_allSymbols[3]  = "NZDUSD";
      m_allSymbols[4]  = "USDCAD";
      m_allSymbols[5]  = "USDCHF";
      m_allSymbols[6]  = "USDJPY";
      m_allSymbols[7]  = "EURGBP";
      m_allSymbols[8]  = "EURAUD";
      m_allSymbols[9]  = "EURNZD";
      m_allSymbols[10] = "EURCAD";
      m_allSymbols[11] = "EURCHF";
      m_allSymbols[12] = "EURJPY";
      m_allSymbols[13] = "GBPAUD";
      m_allSymbols[14] = "GBPNZD";
      m_allSymbols[15] = "GBPCAD";
      m_allSymbols[16] = "GBPCHF";
      m_allSymbols[17] = "GBPJPY";
      m_allSymbols[18] = "AUDNZD";
      m_allSymbols[19] = "AUDCAD";
      m_allSymbols[20] = "AUDCHF";
      m_allSymbols[21] = "AUDJPY";
      m_allSymbols[22] = "NZDCAD";
      m_allSymbols[23] = "NZDCHF";
      m_allSymbols[24] = "NZDJPY";
      m_allSymbols[25] = "CADCHF";
      m_allSymbols[26] = "CADJPY";
      m_allSymbols[27] = "CHFJPY";
      m_allSymbols[28] = "XAUUSD";
     }

                    ~COttoCorrelationFilter(void) { ArrayFree(m_allSymbols); }

   //+------------------------------------------------------------------+
   //| Initialize                                                       |
   //+------------------------------------------------------------------+
   bool              Initialize(string symbol)
     {
      m_symbol = symbol;
      if(EnableLogging)
         Print("[Correlation] Initialized for ", m_symbol,
               " | Matrix: 28-pair + gold decomposition engine");
      return true;
     }

   //+------------------------------------------------------------------+
   //| MODULE 2: IsTradeVetoed — Weighted Portfolio Veto                |
   //+------------------------------------------------------------------+
   bool              IsTradeVetoed(ENUM_TRADE_DIRECTION proposedDirection)
     {
      if(proposedDirection != DIR_LONG && proposedDirection != DIR_SHORT)
         return false;
      int dir = (proposedDirection == DIR_LONG) ? 1 : -1;
      return ScanPositionsForVeto(dir);
     }

   //+------------------------------------------------------------------+
   //| BroadcastBias — write bias to MT5 Global Variable                |
   //| Key is built from the SANITIZED root so that EURUSD.x, EURUSDm   |
   //| and EURUSD all publish to the same "TS_Bias_EURUSD" slot.        |
   //+------------------------------------------------------------------+
   void              BroadcastBias(int bias)
     {
      string cleanSym = CleanSymbol(m_symbol);
      string varName = "TS_Bias_" + cleanSym;   // Key shared across EA instances
      if(bias == 0)
         GlobalVariableDel(varName);
      else
         GlobalVariableSet(varName, bias);
     }

   //+------------------------------------------------------------------+
   //| MODULE 3: GetWeightedBiasSum — matrix-weighted peer bias sum     |
   //| Reads "TS_Bias_<root>" slots published by peer EA instances.     |
   //| INVARIANT: m_allSymbols[] is stored pre-sanitized by the ctor,   |
   //| so array entries can be compared/used directly without cleaning. |
   //+------------------------------------------------------------------+
   int               GetWeightedBiasSum(void)
     {
      int totalBias = 0;
      string myClean = CleanSymbol(m_symbol);
      for(int i = 0; i < ArraySize(m_allSymbols); i++)
        {
         if(m_allSymbols[i] == myClean) continue;
         string varName = "TS_Bias_" + m_allSymbols[i];
         if(GlobalVariableCheck(varName))
           {
            int peerBias = (int)GlobalVariableGet(varName);
            int score = GetCorrelationScore(myClean, m_allSymbols[i]);
            totalBias += peerBias * score;
           }
        }
      return totalBias;
     }

   //+------------------------------------------------------------------+
   //| MODULE 3: ResolveBidirectionalConflict — weighted tie-breaker   |
   //+------------------------------------------------------------------+
   int               ResolveBidirectionalConflict(void)
     {
      int totalBias = GetWeightedBiasSum();
      if(EnableLogging)
         Print("[Correlation] Weighted Bias Sum for ", m_symbol, " = ", totalBias);

      if(totalBias >= 2) return 1;    // Strongly Long -> keep Support
      if(totalBias <= -2) return -1;  // Strongly Short -> keep Resistance
      return 0;                       // Mixed -> delete both
     }

   //+------------------------------------------------------------------+
   //| Public access to matrix score (for diagnostics)                 |
   //+------------------------------------------------------------------+
   int               DebugScore(string sym1, string sym2)
     {
      return GetCorrelationScore(sym1, sym2);
     }
  };

//+------------------------------------------------------------------+
#endif  // __OTTO_CORRELATION_FILTER__
