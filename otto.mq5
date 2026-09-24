//+------------------------------------------------------------------+
//|                                                       OttoEA.mq5 |
//|                    OTTO — Goat Funded Trader (GFT) Master Build    |
//|                    Pine Script Master Build Port (v5.31)            |
//|                                    Institutional / Real-Money    |
//+------------------------------------------------------------------+
#property copyright "OTTO EA - Goat Funded Trader (GFT) Master Build"
#property version   "5.31"
#property description "OTTO EA â€” Goat Funded Trader (GFT) Master Build"
#property description "Separation | Sizing | Front-Run | Near-Miss | Stale vetoes"
#property description "Modules: News Shield | Risk | Block Manager | Order Mgmt | Trail"
#property link      "https://github.com/vickygujjar17/OTTO-.git"

//+------------------------------------------------------------------+
//| Includes                                                          |
//+------------------------------------------------------------------+
#include <Trade\Trade.mqh>
#include <Otto/OttoDefines.mqh>
#include <Otto/COttoNewsFilter.mqh>
#include <Otto/COttoRiskManager.mqh>
#include <Otto/COttoMarketStructure.mqh>
#include <Otto/COttoBlockManager.mqh>
#include <Otto/COttoOrderManager.mqh>
#include <Otto/COttoCorrelationFilter.mqh>
#include <Otto/COttoTradeManager.mqh>
#include <Otto/COttoJournal.mqh>

//+------------------------------------------------------------------+
//| Global Module Instances                                           |
//+------------------------------------------------------------------+
COttoNewsFilter        g_newsFilter;
COttoRiskManager       g_riskManager;
COttoMarketStructure   g_marketStructure;
COttoBlockManager      g_blockManager;
COttoOrderManager      g_orderManager;
COttoCorrelationFilter g_correlationFilter;
COttoTradeManager      g_tradeManager;
COttoJournal           g_journal;

//+------------------------------------------------------------------+
//| Global State                                                      |
//+------------------------------------------------------------------+
string   g_symbol;
bool     g_isHedging        = false;
bool     g_initialized      = false;
int      g_tickCount        = 0;
datetime g_lastStatusLog    = 0;
int      g_fileHandle       = INVALID_HANDLE;
string   g_logFileName      = "";

// --- Human-readable trade journal ---
int      g_journalHandle    = INVALID_HANDLE;
string   g_journalFileName  = "";
long     g_lastPlacedCount  = 0;   // last-seen OrderManager GetOrdersPlaced()
long     g_lastFilledCount  = 0;   // last-seen GetOrdersFilled()
long     g_lastRejectedCount= 0;   // last-seen GetOrdersRejected()
long     g_lastHalfRisk     = 0;   // last-seen GetHalfRiskTriggers()
long     g_lastBE           = 0;   // last-seen GetBreakevenTriggers()
long     g_lastTrail        = 0;   // last-seen GetTrailActivations()
bool     g_lastHadTrade     = false;

// --- Market-day counter (Pine ta.change(time("D")) mirror) ---
int      g_marketDay        = 0;
datetime g_lastDailyBarTime = 0;
datetime g_lastBarTime      = 0;

// --- Prop Firm Safety State ---
double   g_initialBalance      = 0;
// FIX (v5.28): the daily budget now re-baselines at the 5:00 PM NEW YORK
// boundary computed from TimeGMT(), not at the broker's D1 candle open. On a
// GMT+2/+3 feed the two coincide, but on a UTC or local feed the D1 open drifts
// 5-8 hours off the firm's actual reset, handing back (or withholding) drawdown
// budget mid-session.
double   g_dailyResetBalance   = 0;  // Resets at 17:00 New York (EST/EDT aware)
// FIX (v5.29): this HWM is now the basis of the 5% trailing total-DD rule ALONE.
// (v5.24 history: it replaced the peak CLOSED balance held in
// g_highWaterMarkBalance, which was removed rather than left stale.) The 1%
// floating rule was un-merged from it in v5.29: floating loss is the unrealised
// loss on OPEN positions, measured balance-vs-equity. Sharing this HWM made the
// two rules evaluate the SAME quantity, so the 1% threshold sat permanently
// tighter, a routine 1% dip latched a permanent halt, and the 5% check below
// was unreachable dead code.
double   g_equityHighWaterMark = 0;  // Tracks highest all-time EQUITY for Trailing Drawdown
// FIX (v5.28): renamed from g_lastMidnightCheck. The anchor is no longer a
// "midnight" of any kind -- it is the exact epoch second of the most recent
// 17:00 New York boundary, derived from the GMT clock and therefore identical
// on every broker feed. Value is stable for a full 24h so the `!=` guard in
// CheckDailyReset() fires exactly once per session.
datetime g_last5pmEstReset     = 0;
bool     g_dailyDD_Paused      = false;
bool     g_totalDD_Halted      = false;
datetime g_dailyDD_ResumeTime  = 0;

//+------------------------------------------------------------------+
//| Prop-firm persistent state key helpers (v5.25)                    |
//|                                                                   |
//| The EA is deployed on a VPS and WILL be restarted mid-session.     |
//| Re-seeding the drawdown baselines from live account values on every |
//| init silently resets the daily 3% budget and re-bases the trailing |
//| limits downward, so the baselines are persisted in MT5 Global      |
//| Variables instead.                                                 |
//|                                                                   |
//| Keys are namespaced OTTO_* and suffixed with the account login, so  |
//| the ~28 EA instances and any other account on the same terminal    |
//| cannot collide. COttoCorrelationFilter owns the TS_Bias_* namespace |
//| and is deliberately not touched.                                   |
//|                                                                   |
//| NOTE: Globals live in the TERMINAL's shared namespace, not per     |
//| chart. Two terminals on one machine have separate stores; two charts|
//| of the same account in ONE terminal intentionally share these keys. |
//+------------------------------------------------------------------+
string OttoGvName(const string key)
  {
   return "OTTO_" + key + "_" + IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN));
  }

//| Read a persisted double, falling back when the key is absent.     |
double OttoGvLoadDouble(const string key, const double fallback)
  {
   string name = OttoGvName(key);
   if(!GlobalVariableCheck(name))
      return fallback;
   double v = GlobalVariableGet(name);
   // A GV deleted by the terminal or never set reads back as 0.0; treat
   // that as absent rather than as a legitimate baseline.
   if(v == 0.0)
      return fallback;
   return v;
  }

//| Read a persisted boolean flag (stored as 0.0 / 1.0).              |
bool OttoGvLoadFlag(const string key, const bool fallback)
  {
   string name = OttoGvName(key);
   if(!GlobalVariableCheck(name))
      return fallback;
   return (GlobalVariableGet(name) >= 0.5);
  }

//| Write/persist a double. Returns false if the terminal refused it. |
//| NOTE: GlobalVariableSet returns datetime (last-access time), not bool. |
bool OttoGvStore(const string key, const double value)
  {
   if(!GlobalVariableSet(OttoGvName(key), value))
     {
      Print("[Safety] WARNING: GlobalVariableSet failed for ", OttoGvName(key));
      return false;
     }
   return true;
  }

//| Persist one boolean flag as 0.0 / 1.0.                            |
bool OttoGvStoreFlag(const string key, const bool value)
  {
   if(!GlobalVariableSet(OttoGvName(key), value ? 1.0 : 0.0))
     {
      Print("[Safety] WARNING: GlobalVariableSet failed for ", OttoGvName(key));
      return false;
     }
   return true;
  }

//+------------------------------------------------------------------+
//| New York session clock (v5.28)                                    |
//|                                                                   |
//| GFT's daily drawdown counter resets at 5:00 PM New York, which is  |
//| 5:00 PM EST (UTC-5) in winter and 5:00 PM EDT (UTC-4) in summer.   |
//| The previous build inferred that boundary from iTime(PERIOD_D1,0), |
//| i.e. the broker's own midnight -- a different wall-clock moment on |
//| any feed that is not GMT+2/+3. The helpers below derive the true   |
//| boundary from TimeGMT() so the reset lands on the same instant     |
//| regardless of broker server offset.                                |
//|                                                                   |
//| US DST rule (since 2007): starts 02:00 LOCAL on the 2nd Sunday of  |
//| March, ends 02:00 LOCAL on the 1st Sunday of November. Expressed   |
//| in UTC those transitions are 07:00 and 06:00 respectively. A 17:00 |
//| boundary is never adjacent to a transition, so the offset in force |
//| at the boundary instant is always the offset that applies to it.   |
//+------------------------------------------------------------------+

//| Day-of-month of the nth Sunday in the given month of the year.   |
//| Returns 0 if the month cannot contain that many Sundays.         |
int OttoNthSunday(int year, int month, int nth)
  {
   int seen = 0;
   for(int d = 1; d <= 31; d++)
     {
      MqlDateTime dt;
      dt.year = year;
      dt.mon  = month;
      dt.day  = d;
      dt.hour = 0;
      dt.min  = 0;
      dt.sec  = 0;
      MqlDateTime out;
      TimeToStruct(StructToTime(dt), out);
      // StructToTime normalises an out-of-range day into the next month;
      // that is the signal to stop scanning.
      if(out.mon != month)
         break;
      if(out.day_of_week == 0)
        {
         seen++;
         if(seen == nth)
            return d;
        }
     }
   return 0;
  }

//| UTC instant of a Y/M/D HH:MM:00 stamp (used for DST transitions). |
datetime OttoUtcStamp(int year, int month, int day, int hour)
  {
   MqlDateTime dt;
   dt.year = year;
   dt.mon  = month;
   dt.day  = day;
   dt.hour = hour;
   dt.min  = 0;
   dt.sec  = 0;
   return StructToTime(dt);
  }

//| True when the UTC instant falls inside US daylight-saving time.   |
bool OttoIsNewYorkDst(datetime gmt)
  {
   MqlDateTime dt;
   TimeToStruct(gmt, dt);
   int year = dt.year;

   int startDay = OttoNthSunday(year, 3, 2);    // 2nd Sunday of March
   int endDay   = OttoNthSunday(year, 11, 1);   // 1st Sunday of November
   if(startDay == 0 || endDay == 0)
      return false;                             // degenerate: assume EST

   // 02:00 EST == 07:00 UTC; 02:00 EDT == 06:00 UTC.
   datetime dstStart = OttoUtcStamp(year, 3,  startDay, 7);
   datetime dstEnd   = OttoUtcStamp(year, 11, endDay,   6);
   return (gmt >= dstStart && gmt < dstEnd);
  }

//| Current wall-clock time on the New York floor (EST/EDT aware).    |
datetime OttoNewYorkTime(datetime gmt)
  {
   int offsetHours = OttoIsNewYorkDst(gmt) ? 4 : 5;
   return gmt - (datetime)(offsetHours * 3600);
  }

//| Most recent 17:00:00 New York boundary, returned as a UTC epoch.  |
//|                                                                   |
//| On a weekend or before 17:00 NY this resolves to the PREVIOUS      |
//| weekday's boundary, exactly like a firm-side session clock: the     |
//| value changes once per calendar day at 17:00 and is constant in     |
//| between, so the `!=` guard in CheckDailyReset() fires once per      |
//| session and never mid-session.                                     |
datetime OttoLast5pmNewYork(datetime gmt)
  {
   datetime ny = OttoNewYorkTime(gmt);

   MqlDateTime dt;
   TimeToStruct(ny, dt);
   dt.hour = 17;
   dt.min  = 0;
   dt.sec  = 0;
   datetime boundaryNy = StructToTime(dt);   // 17:00 NY on this NY date
   if(boundaryNy > ny)
      boundaryNy -= 86400;                   // today's boundary is still ahead

   // Map the NY wall-clock boundary back to UTC using the offset in force
   // AT the boundary. 17:00 sits far from any transition, so the offset read
   // at the first candidate is already the correct one.
   datetime gmtEst = boundaryNy + 5 * 3600;
   return OttoIsNewYorkDst(gmtEst) ? (boundaryNy + 4 * 3600) : gmtEst;
  }

//+------------------------------------------------------------------+
//| Persist every prop-firm safety baseline (v5.25).                   |
//| Called whenever a baseline moves, so a restart always resumes from |
//| the true rather than the current account state.                    |
//+------------------------------------------------------------------+
void PersistSafetyState(void)
  {
   OttoGvStore("DailyReset", g_dailyResetBalance);
   OttoGvStore("HighWater",  g_equityHighWaterMark);
   // FIX (v5.28): key renamed LastMid -> Last5pmReset to match what the value
   // actually holds. The old key name is abandoned rather than migrated: an
   // existing LastMid stamp would be a broker-midnight value that is not a
   // valid 17:00-NY boundary, so treating it as absent lets OnInit() re-anchor
   // once. On the next tick CheckDailyReset() re-persists under the new key.
   OttoGvStore("Last5pmReset", (double)g_last5pmEstReset);
   OttoGvStoreFlag("Paused", g_dailyDD_Paused);
   OttoGvStoreFlag("Halted", g_totalDD_Halted);
  }

//+------------------------------------------------------------------+
//| int OnInit(void)
//+------------------------------------------------------------------+
int OnInit(void)
  {
   g_symbol = _Symbol;

   Print("==============================================================");
   Print("  OTTO EA v5.31 — 28-Pair Institutional Master Build — INITIALIZING");
   Print("  Symbol: ", g_symbol, " | Magic: ", MagicNumber);
   Print("==============================================================");

   // --- Validate Hedging Account ---
   ENUM_ACCOUNT_MARGIN_MODE marginMode = (ENUM_ACCOUNT_MARGIN_MODE)AccountInfoInteger(ACCOUNT_MARGIN_MODE);
   if(marginMode != ACCOUNT_MARGIN_MODE_RETAIL_HEDGING)
     {
      Print("FATAL: OTTO requires HEDGING account mode!");
      Print("Current mode: ", EnumToString(marginMode));
      return INIT_FAILED;
     }
   g_isHedging = true;
   Print("[INIT] Account type: HEDGING");

   // --- GOLD DEPLOYMENT KILL-SWITCH ---------------------------------
   // OTTO is strictly forbidden from executing on Gold. The instrument's
   // volatility profile and tick-value characteristics invalidate the
   // Pine-derived separation/sizing model this build is calibrated on, and
   // its stop distances routinely breach broker minimums -- so a Gold chart
   // is refused outright rather than run in a degraded mode.
   // Correlation tracking for Gold positions held OUTSIDE this EA (manual
   // or other-magic) remains fully active: COttoCorrelationFilter reads the
   // broker position book independently of this guard.
   string symUpper = g_symbol;
   StringToUpper(symUpper);
   if(StringFind(symUpper, "XAU") >= 0 || StringFind(symUpper, "GOLD") >= 0)
     {
      Print("==============================================================");
      Print("  FATAL: OTTO is FORBIDDEN from executing on Gold charts.");
      Print("  Symbol detected: ", g_symbol);
      Print("  This EA must not trade XAU* / GOLD*." );
      Print("  Correlation tracking for externally-held Gold positions");
      Print("  remains active and is unaffected by this refusal.");
      Print("  Move the EA to a permitted instrument and re-initialise.");
      Print("==============================================================");
      return INIT_FAILED;
     }
   Print("[INIT] Gold kill-switch: ", g_symbol, " permitted");

   Print("[INIT] Balance: ", DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE), 2),
         " | Equity: ", DoubleToString(AccountInfoDouble(ACCOUNT_EQUITY), 2),
         " | Free: ", DoubleToString(AccountInfoDouble(ACCOUNT_MARGIN_FREE), 2));

   // --- News Filter ---
   if(!g_newsFilter.Initialize(g_symbol))
      Print("[INIT] WARNING: News Filter init failed â€” continuing");
   else
      Print("[INIT] News Filter OK");

   // --- Risk Manager ---
   if(!g_riskManager.Initialize(g_symbol))
     {
      Print("[INIT] FATAL: Risk Manager init failed");
      return INIT_FAILED;
     }
   Print("[INIT] Risk Manager OK");

   // --- Market Structure (pivots) ---
   if(!g_marketStructure.Initialize(g_symbol))
     {
      Print("[INIT] FATAL: Market Structure init failed");
      return INIT_FAILED;
     }
   Print("[INIT] Market Structure OK");

   // --- Block Manager ---
   if(!g_blockManager.Initialize(g_symbol, &g_marketStructure))
     {
      Print("[INIT] FATAL: Block Manager init failed");
      return INIT_FAILED;
     }
   Print("[INIT] Block Manager OK");

   // --- Startup historical warm-up: populate blocks from past pivots ---
   g_blockManager.WarmUpHistory();
   Print("[INIT] Warm-up complete — blocks now on chart: ",
         g_blockManager.GetBlockCount());


   // --- Correlation Filter ---
   if(!g_correlationFilter.Initialize(g_symbol))
      Print("[INIT] WARNING: Correlation Filter init failed â€” continuing");
   else
      Print("[INIT] Correlation Filter OK");

   // --- Order Manager ---
   if(!g_orderManager.Initialize(g_symbol, &g_riskManager, &g_blockManager, &g_correlationFilter))
     {
      Print("[INIT] FATAL: Order Manager init failed");
      return INIT_FAILED;
     }
   Print("[INIT] Order Manager OK");

// --- Trade Journal (human-readable .txt lifecycle log) ---
   g_journal.Initialize(g_symbol, &g_blockManager);
   g_orderManager.SetJournal(&g_journal);


   // --- Trade Manager ---
   // v5.27: the filter is injected so Update() can run the quorum guard.
   if(!g_tradeManager.Initialize(g_symbol, &g_riskManager, &g_orderManager, &g_blockManager,
                               &g_correlationFilter))
     {
      Print("[INIT] FATAL: Trade Manager init failed");
      return INIT_FAILED;
     }
   Print("[INIT] Trade Manager OK");

   // --- Prop firm safety state: PERSISTENT MEMORY (v5.25) ---
   // FIX (v5.25): these baselines are loaded from MT5 GlobalVariables and only
   // seeded when absent. Previously every init re-seeded them from the LIVE
   // account, so a VPS restart mid-session reset the 3% daily budget and
   // re-based the trailing limits downward -- "drawdown amnesia".
   //
   // The live trailing basis is g_equityHighWaterMark (single equity HWM, v5.24,
   // shared by the 5% trailing DD basis; the 1% floating rule no longer shares it, v5.29). The directive that
   // requested this patch referred to a global named `g_highWaterMark`, which
   // does not exist in this build; persisting a separate new global under that
   // name would compile while leaving the REAL basis unpersisted, so the correct
   // target is used deliberately here.
   g_initialBalance = AccountInfoDouble(ACCOUNT_BALANCE);

   double liveEquity = AccountInfoDouble(ACCOUNT_EQUITY);

   // --- Daily reset anchor (3% daily DD basis) ---
   // FIX (v5.28): the anchor is the most recent 17:00 New York boundary, taken
   // from TimeGMT() rather than from the broker's D1 candle open. The stored
   // stamp is re-seeded when absent OR when it precedes the boundary that is in
   // force right now -- i.e. the session rolled over while the EA was down, so
   // the persisted budget belongs to a session that has already ended. The same
   // test covers a prop firm resetting the challenge account on the same login:
   // a reset always lands on a later calendar day, so the stamp is stale too.
   datetime nyBoundaryNow = OttoLast5pmNewYork(TimeGMT());
   g_last5pmEstReset = (datetime)OttoGvLoadDouble("Last5pmReset", (double)nyBoundaryNow);
   bool staleAnchor    = (g_last5pmEstReset < nyBoundaryNow);
   double storedDaily  = OttoGvLoadDouble("DailyReset", liveEquity);
   if(staleAnchor)
      g_dailyResetBalance = AccountInfoDouble(ACCOUNT_BALANCE);
   else
      g_dailyResetBalance = storedDaily;
   if(g_dailyResetBalance <= 0)
      g_dailyResetBalance = AccountInfoDouble(ACCOUNT_BALANCE);

   // --- Trailing high-water mark (5% trailing DD + 1% floating basis) ---
   // A stored HWM BELOW current equity is simply stale history (the account has
   // since made new highs) and is superseded by live equity. A stored HWM far
   // ABOVE current equity is honoured as a genuine prior peak, because that is
   // precisely the state this fix exists to remember -- a restart while down.
   double storedHwm = OttoGvLoadDouble("HighWater", liveEquity);
   g_equityHighWaterMark = (storedHwm > liveEquity) ? storedHwm : liveEquity;

   // --- Halt / pause latches ---
   // g_totalDD_Halted is PERMANENT: a breached account must stay halted across a
   // restart, otherwise a VPS bounce would silently resume trading past a
   // hard limit. g_dailyDD_Paused clears at the session rollover (see
   // CheckDailyReset) and is recomputed here from the persisted anchor.
   // FIX (v5.29): this latch is now armed ONLY by the 5% trailing total-DD
   // check. The 1% floating-loss rule no longer sets it, so a floating dip cuts
   // the basket and trading continues instead of halting the account for good.
   g_totalDD_Halted = OttoGvLoadFlag("Halted", false);
   g_dailyDD_Paused = OttoGvLoadFlag("Paused", false) && !staleAnchor;
   g_dailyDD_ResumeTime = (g_dailyDD_Paused && g_last5pmEstReset > 0)
                          ? g_last5pmEstReset + 86400 : 0;

   // Persist the reconciled baselines so the store matches in-memory truth.
   PersistSafetyState();

   Print("[Safety] Persistence: ", staleAnchor ? "STALE ANCHOR RE-SEEDED" : "restored",
         " | DailyReset: ", DoubleToString(g_dailyResetBalance, 2),
         " (stored ", DoubleToString(storedDaily, 2), ")",
         " | Equity HWM: ", DoubleToString(g_equityHighWaterMark, 2),
         " (stored ", DoubleToString(storedHwm, 2), ")");
   Print("[Safety] Restored latches: Paused=", g_dailyDD_Paused ? "true" : "false",
         " Halted=", g_totalDD_Halted ? "true" : "false",
         " | Last5pmReset: ", TimeToString(g_last5pmEstReset, TIME_DATE|TIME_MINUTES));
   Print("[Safety] Init Balance: ", DoubleToString(g_initialBalance, 2),
         " | DailyDD: ", SafetyDailyDDLimit, "% | TotalDD(trailing): ", SafetyTotalDDLimit,
         "% | Floating: ", SafetyMaxFloatingLoss, "% of balance");
   int    serverOffset = (int)(TimeTradeServer() - TimeGMT());
   Print("[Safety] Daily reset anchor (17:00 NY): ",
         TimeToString(g_last5pmEstReset, TIME_DATE|TIME_MINUTES), " GMT",
         " (server ", TimeToString((datetime)(g_last5pmEstReset + serverOffset),
                                   TIME_DATE|TIME_MINUTES), ")");

   // --- Market-day counter init (mirrors ta.change(time("D"))) ---
   g_lastDailyBarTime = iTime(_Symbol, PERIOD_D1, 0);
   g_marketDay        = 0;
   g_lastBarTime      = iTime(_Symbol, PERIOD_CURRENT, 0);

   // --- Sync existing trade state ---
   g_tradeManager.SyncTradeState();

   // --- Setup log file ---
   if(EnableLogging)
     {
      g_logFileName = "Otto_" + g_symbol + "_" +
                      IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN)) + ".csv";
      g_fileHandle  = FileOpen(g_logFileName, FILE_CSV | FILE_WRITE | FILE_SHARE_READ, ',');
      if(g_fileHandle != INVALID_HANDLE)
        {
         FileWrite(g_fileHandle, "Time", "Symbol", "Event", "Details");
         Print("[INIT] Log file: ", g_logFileName);
        }
      else
         Print("[INIT] WARNING: Could not create log file");
     }

// --- Setup human-readable trade journal ---
   if(EnableJournal)
     {
      g_journalFileName = "Otto_Journal_" + g_symbol + "_" +
                          IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN)) + ".txt";
      g_journalHandle   = FileOpen(g_journalFileName, FILE_TXT | FILE_WRITE | FILE_SHARE_READ | FILE_ANSI);
      if(g_journalHandle != INVALID_HANDLE)
        {
         FileWriteString(g_journalHandle, "OTTO EA - Trade Journal\n");
         FileWriteString(g_journalHandle, "Symbol: " + g_symbol + " | Started: " + TimeToString(TimeCurrent()) + "\n");
         FileWriteString(g_journalHandle, "------------------------------------------------------------------\n");
         Print("[INIT] Journal file: ", g_journalFileName);
        }
      else
         Print("[INIT] WARNING: Could not create journal file");
     }
   g_initialized = true;

   Print("==============================================================");
   Print("  OTTO EA INITIALIZED SUCCESSFULLY");
   Print("  Risk: ", (InpFixedRiskUSD > 0 ? ("$" + DoubleToString(InpFixedRiskUSD,2))
                                          : (DoubleToString(RiskPercent,2) + "%")),
         " | DailyDD: ", SafetyDailyDDLimit, "% | TotalDD: ", SafetyTotalDDLimit, "%");
   Print("==============================================================");

   return INIT_SUCCEEDED;
  }


//+------------------------------------------------------------------+
//| Expert deinitialization function                                  |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   Print("==============================================================");
   Print("  OTTO EA â€” DEINITIALIZING (reason ", reason, ")");

   Print("  --- News Filter ---");
   Print("  Blackouts: ", g_newsFilter.GetBlackoutCount());
   Print("  --- Block Manager ---");
   Print("  Created: ", g_blockManager.GetBlocksCreated(),
         " | Broken: ", g_blockManager.GetBlocksBroken(),
         " | Vetoed: ", g_blockManager.GetBlocksVetoed());
   Print("  --- Order Manager ---");
   Print("  Placed: ", g_orderManager.GetOrdersPlaced(),
         " | Filled: ", g_orderManager.GetOrdersFilled(),
         " | Rejected: ", g_orderManager.GetOrdersRejected());
   Print("  --- Trade Manager ---");
   Print("  HalfRisk: ", g_tradeManager.GetHalfRiskTriggers(),
         " | BE: ", g_tradeManager.GetBreakevenTriggers(),
         " | Trail: ", g_tradeManager.GetTrailActivations());

   // Cancel all pending orders
   int pending = g_orderManager.CountMyPending();
   if(pending > 0)
     {
      Print("  Cancelling ", pending, " pending orders...");
      g_orderManager.CancelAllPendingOrders();
     }

   // Close log file
   if(g_fileHandle != INVALID_HANDLE)
     {
      FileClose(g_fileHandle);
      g_fileHandle = INVALID_HANDLE;
     }
// Close journal file
   if(g_journalHandle != INVALID_HANDLE)
     {
      FileClose(g_journalHandle);
      g_journalHandle = INVALID_HANDLE;
     }
   g_journal.Close();   // COttoJournal detailed .txt journal

   g_initialized = false;
   Print("==============================================================");
  }

//+------------------------------------------------------------------+
//| Checks the daily reset at 5:00 PM NEW YORK (v5.28)                |
//+------------------------------------------------------------------+
void CheckDailyReset(void)
  {
   // FIX (v5.28): the boundary is derived from TimeGMT(), not from
   // iTime(PERIOD_D1,0). The D1 open is the BROKER's midnight, which coincides
   // with 5:00 PM New York only on a GMT+2/+3 feed (the previous comment here
   // asserted that identity unconditionally, and it is false on a UTC or local
   // feed). Because the anchor is now offset-independent it is identical on
   // every chart, and its value is constant for a full 24h, so the `!=` guard
   // below fires exactly once per session -- the tick-by-tick re-baselining that
   // v5.23 fixed cannot return through this path.
   //
   // TimeGMT() is the terminal's own clock corrected for NTP; it does not need
   // broker history, so unlike the old iTime() form this is valid from the very
   // first tick and the previous "iTime returned 0 while D1 loads" guard is gone.
   datetime nyBoundary = OttoLast5pmNewYork(TimeGMT());

   // A boundary of 0 could only come from a catastrophic clock fault; refusing
   // it keeps a bad read from re-baselining the budget on every tick.
   if(nyBoundary == 0)
      return;

   if(nyBoundary != g_last5pmEstReset)
     {
      g_dailyResetBalance = AccountInfoDouble(ACCOUNT_BALANCE);
      g_last5pmEstReset = nyBoundary;
      g_dailyDD_ResumeTime = 0;   // pause window ended with the session

      if(g_dailyDD_Paused)
        {
         g_dailyDD_Paused = false;
         if(EnableLogging)
            Print("[Safety] New day (17:00 New York) — daily DD pause LIFTED");
        }

      // FIX (v5.25): persist the new session baselines. Without this the
      // GlobalVariables would still hold yesterday's anchor, and a restart
      // after the rollover would restore a stale daily basis.
      // NOTE: g_totalDD_Halted is deliberately NOT cleared here. The 5% trailing
      // breach is permanent for the life of the account; only the daily pause is
      // a per-session state. Clearing the halt on rollover would let a breached
      // account resume trading at the next boundary.
      PersistSafetyState();
     }
  }

//+------------------------------------------------------------------+
//| New-bar detection â€” gates the block formation + veto funnel      |
//+------------------------------------------------------------------+
bool IsNewBar(void)
  {
   datetime barTime = iTime(_Symbol, PERIOD_CURRENT, 0);
   if(barTime == g_lastBarTime)
      return false;
   g_lastBarTime = barTime;
   return true;
  }

//+------------------------------------------------------------------+
//| Market-day counter â€” increments when the daily bar changes.      |
//| Weekends are naturally skipped (no daily bars on Sat/Sun). This  |
//| mirrors Pine ta.change(time("D")).                               |
//+------------------------------------------------------------------+
void UpdateMarketDay(void)
  {
   datetime dailyBar = iTime(_Symbol, PERIOD_D1, 0);
   if(dailyBar != g_lastDailyBarTime)
     {
      g_marketDay++;
      g_lastDailyBarTime = dailyBar;
     }
  }

//+------------------------------------------------------------------+
//| Hive Mind â€” broadcast bias and resolve bidirectional conflicts   |
//+------------------------------------------------------------------+
void HiveMind(void)
  {
   if(g_dailyDD_Paused) return;

   SSniperBlock allBlocks[];
   int total = g_blockManager.GetAllBlocks(allBlocks);
   bool hasSupport = false, hasResistance = false;
   for(int b = 0; b < total; b++)
     {
      if(allBlocks[b].isVetoed) continue;
      if(allBlocks[b].type == BLOCK_SUPPORT) hasSupport = true;
      if(allBlocks[b].type == BLOCK_RESISTANCE) hasResistance = true;
     }

   int myBias = 0;
   if(hasSupport && !hasResistance)
      myBias = 1;
   else if(hasResistance && !hasSupport)
      myBias = -1;
   else if(hasSupport && hasResistance)
     {
      int resolution = g_correlationFilter.ResolveBidirectionalConflict();
      if(resolution == 1)
        {
         g_blockManager.DeleteBlockType(BLOCK_RESISTANCE);
         myBias = 1;
         if(EnableLogging) Print("[HiveMind] CONFLICT: peers Long â†’ kept Support");
        }
      else if(resolution == -1)
        {
         g_blockManager.DeleteBlockType(BLOCK_SUPPORT);
         myBias = -1;
         if(EnableLogging) Print("[HiveMind] CONFLICT: peers Short â†’ kept Resistance");
        }
      else
        {
         g_blockManager.DeleteBlockType(BLOCK_SUPPORT);
         g_blockManager.DeleteBlockType(BLOCK_RESISTANCE);
         myBias = 0;
         if(EnableLogging) Print("[HiveMind] CONFLICT: no consensus â†’ deleted both");
        }
     }
   g_correlationFilter.BroadcastBias(myBias);
  }


//+------------------------------------------------------------------+
//| Expert tick function â€” Main Orchestration Loop                   |
//+------------------------------------------------------------------+
void OnTick(void)
  {
   if(!g_initialized) return;
   if(g_totalDD_Halted) return;

   g_tickCount++;
   CheckDailyReset();

   // v5.31: open a fresh per-tick deletion cycle. Placed here, ahead of every
   // sweeper (including the CancelOrdersForInvalidBlocks() call inside the
   // IsNewBar() block below), because MT5 does not refresh its order list
   // within a single event handler: two sweeps would otherwise both dispatch
   // TRADE_ACTION_REMOVE for the same ticket and the second draw an
   // "[Invalid request]" rejection.
   g_orderManager.BeginOrderCycle();

   // ================================================================
   // STEP 0: PROP FIRM SAFETY CHECKS (highest priority)
   // ================================================================
   if(!g_dailyDD_Paused && !g_totalDD_Halted)
     {
      // FIX (v5.29): both account figures are read once, here, before any rule
      // consumes them. (v5.29 regression: the floating-loss rewrite replaced the
      // comment block under this line and dropped the `equity` declaration with
      // it, orphaning the eight downstream uses below.)
      double equity  = AccountInfoDouble(ACCOUNT_EQUITY);
      double balance = AccountInfoDouble(ACCOUNT_BALANCE);

      // FIX (v5.24): single equity high-water mark. The trailing total-DD limit
      // previously trailed the peak CLOSED balance; it now trails peak EQUITY,
      // matching GFT's all-time-equity trailing drawdown. This is the basis of
      // the 5% trailing rule ALONE (v5.29); only ratchets UP, never down.
      // FIX (v5.25): each new peak is persisted immediately, so a restart while
      // the account is down from its high resumes from the TRUE peak instead of
      // re-basing the trailing floor to the depressed live equity.
      if(equity > g_equityHighWaterMark)
        {
         g_equityHighWaterMark = equity;
         OttoGvStore("HighWater", g_equityHighWaterMark);
        }

      double dailyDD = (g_dailyResetBalance > 0) ? 100.0 * (g_dailyResetBalance - equity) / g_dailyResetBalance : 0;
      double totalDD = (g_equityHighWaterMark > 0) ? 100.0 * (g_equityHighWaterMark - equity) / g_equityHighWaterMark : 0;
      // FIX (v5.29): "floating loss" is the UNREALISED loss on currently open
      // positions, so it is measured against the CLOSED balance:
      //     floating = (balance - equity) / balance
      // The previous build measured a TRAILING retracement from
      // g_equityHighWaterMark -- exactly the quantity totalDD computes above.
      // Three consequences of that basis are all removed here:
      //   * the two rules were the same quantity, so the 1% threshold sat
      //     permanently tighter and the 5% check was unreachable;
      //   * a 1% dip from the equity peak fired with NO position open, where
      //     there is by definition no floating loss at all;
      //   * the branch latched g_totalDD_Halted (persisted, restored on every
      //     init), so an ordinary dip permanently bricked the account.
      // The corrected ratio cannot reproduce the false positive: when the book
      // is flat, equity == balance and it reads exactly 0.
      // (equity / balance are declared once at the top of this block.)
      double floatingLoss = (balance > 0 && equity < balance)
                            ? 100.0 * (balance - equity) / balance
                            : 0.0;

      // 1% Max Floating Loss Rule -- INTERNAL trade-management rule (v5.29).
      // On breach: cancel resting orders, close the whole basket to protect
      // capital, then RETURN FOR THIS TICK ONLY. Nothing is latched and no
      // cooldown is imposed, so the next tick resumes scanning for setups.
      // FIX (v5.29): deliberately does NOT arm g_totalDD_Halted. That latch is
      // persisted and restored on every init, so arming it here (as the previous
      // build did) turned an ordinary floating dip into a permanent halt. The 5%
      // trailing check below is now the ONLY permanent halt -- which is correct,
      // because that is GFT's actual account-ending hard limit.
      if(floatingLoss >= SafetyMaxFloatingLoss)
        {
         g_orderManager.CancelAllPendingOrders();
         // Close the WHOLE basket: hedging-mode pyramid tranches are separate
         // positions and must not survive the cut.
         if(g_orderManager.HasActiveTrade() || g_orderManager.CountOpenPositions() > 0)
            g_orderManager.CloseEntireBasket("1% Max Floating Loss Cut");

         Print("==============================================================");
         Print("  FLOATING LOSS CUT: ", DoubleToString(floatingLoss, 2), "% >= ",
               DoubleToString(SafetyMaxFloatingLoss, 2),
               "% -- basket closed, trading continues");
         Print("  Balance: ", DoubleToString(balance, 2),
               " | Equity: ", DoubleToString(equity, 2));
         Print("==============================================================");
         return;   // this tick only; no latch, no cooldown
        }

      // 3% Max Daily Drawdown (soft breach: pause new orders only)
      if(dailyDD >= SafetyDailyDDLimit)
        {
         g_dailyDD_Paused = true;
         g_dailyDD_ResumeTime = g_last5pmEstReset + 86400;
         OttoGvStoreFlag("Paused", true);   // FIX (v5.25): survives a restart
         g_orderManager.CancelAllPendingOrders();
         if(EnableLogging)
            Print("[Safety] DAILY DRAWDOWN: ", DoubleToString(dailyDD, 2),
                  "% >= ", SafetyDailyDDLimit, "% — paused new orders");
        }

      // 5% Trailing Total Drawdown (hard breach: close all + halt)
      if(totalDD >= SafetyTotalDDLimit)
        {
         g_totalDD_Halted = true;
         OttoGvStoreFlag("Halted", true);   // FIX (v5.25): survives a restart
         g_orderManager.CancelAllPendingOrders();
         // Close the WHOLE basket, not just the primary ticket: hedging-mode
         // pyramid tranches are separate positions and must not survive the halt.
         if(g_orderManager.HasActiveTrade() || g_orderManager.CountOpenPositions() > 0)
            g_orderManager.CloseEntireBasket("Total Trailing DD Halt");
         Print("==============================================================");
         Print("  FATAL: TOTAL TRAILING DRAWDOWN LIMIT REACHED — EA PERMANENTLY HALTED");
         Print("  Equity HWM: ", DoubleToString(g_equityHighWaterMark, 2),
               " | Equity: ", DoubleToString(equity, 2));
         Print("  DD: ", DoubleToString(totalDD, 2), "% >= ", SafetyTotalDDLimit, "%");
         Print("==============================================================");
         return;
        }
     }

   // ================================================================
   // STEP 1: News Filter — DISABLED in v5.00
   // EnableNewsFilter defaults to false and the calendar update call is
   // bypassed entirely so no MQL5 economic-calendar queries are made.
   // ================================================================
   // g_newsFilter.Update();

   // ================================================================
   // STEP 2: NEW BAR â€” block formation + veto funnel (bar-close logic)
   // Mirrors calc_on_every_tick=false.
   // ================================================================
   if(IsNewBar())
     {
      UpdateMarketDay();               // advance the trading-day counter

      // Cancel orders on freshly-invalidated blocks BEFORE the funnel reaps them
      g_orderManager.CancelOrdersForInvalidBlocks();

      // Run the funnel (W1/W2 formation + all v4.70 vetoes)
      g_blockManager.Update(g_marketDay);

      // Cancel any orders declared invalid during this bar's funnel
      g_orderManager.CancelOrdersForInvalidBlocks();

      // Hive Mind (correlation tie-breaker)
      HiveMind();
     }


   // ================================================================
   // STEP 3: INTRA-BAR TARGET CHECKS (inside OnTick)
   // Front-Run 1:3 target + 6-day near-miss expiry, live.
   // ================================================================
   g_blockManager.CheckVetoesInTick(g_marketDay);
   g_orderManager.CancelOrdersForInvalidBlocks();

   // ================================================================
   // STEP 3b: v5.26 CURRENCY-VECTOR CONSENSUS REFRESH
   // Rebuilds the 8-currency vectors from the portfolio book and
   // re-normalizes the 28-pair consensus. Must run BEFORE order
   // placement so the consensus veto reads fresh state, and before the
   // opposing-pending sweep so cancellations use the same snapshot.
   // Cheap: one pass over positions + pendings, no history reads except
   // the two cached anchor symbols.
   // ================================================================
   g_correlationFilter.RefreshVectorState();

   // Strict outlier cancellation: drop our own pendings that now fight
   // the refreshed consensus.
   g_orderManager.CancelOpposingConsensusOrders();

   // v5.27: LIVE 4-PAIR QUORUM sweep. Independent of the consensus veto above:
   // that one reads the portfolio vector snapshot, this one counts live peer
   // agreement on InpQuorumTimeframe. Runs UNGATED for pendings only - a
   // resting order that would fill into a multi-pair reversal is pulled on the
   // quorum verdict alone, before it opens exposure.
   g_orderManager.CancelQuorumOpposingOrders();

   // ================================================================
   // STEP 4: ORDER PLACEMENT â€” Pine-gated limit orders for armed blocks
   // Gated further by news blackout + daily-DD pause.
   // ================================================================
   if(!g_newsFilter.IsInNewsBlackout() && !g_dailyDD_Paused)
      g_orderManager.PlaceOrdersForArmedBlocks();

   // ================================================================
   // STEP 5: MANAGE ACTIVE TRADES â€” dynamic trail (every tick)
   // ================================================================
   g_tradeManager.Update();

   // ================================================================
   // STEP 6: ORDER LIFECYCLE â€” fill detection + reversal completion
   // ================================================================
   g_orderManager.Update();

   // ================================================================
   // STEP 7: DIRECTION CONFLICT â€” cancel same-direction pending orders
   // ================================================================
   g_orderManager.ManageDirectionConflict();

// ================================================================
   // STEP 7B: TRADE JOURNAL — one-shot human-readable event log
   // ================================================================
   if(EnableJournal)
      JournalCheckEvents();
   // ================================================================
   // STEP 8: PERIODIC STATUS LOGGING
   // ================================================================
   if(EnableLogging && TimeCurrent() - g_lastStatusLog >= 3600)
     {
      LogStatus();
      g_lastStatusLog = TimeCurrent();
     }
  }

//+------------------------------------------------------------------+
//| Periodic status logging to console and file                      |
//+------------------------------------------------------------------+
void LogStatus(void)
  {
   string statusLine;
   StringConcatenate(statusLine,
                     "[STATUS] Day=", g_marketDay,
                     " | News: ", g_newsFilter.IsInNewsBlackout() ? "BLACKOUT" : "CLEAR",
                     " | Blocks: ", g_blockManager.GetBlockCount(),
                     " | ActiveTrade: ", g_orderManager.HasActiveTrade() ? "YES" : "NO",
                     " | Pending: ", g_orderManager.CountMyPending(),
                     " | ATR: ", DoubleToString(g_blockManager.GetATR(), _Digits),
                     " | DailyDD: ", g_dailyDD_Paused ? "PAUSED" : "OK",
                     " | TotalDD: ", g_totalDD_Halted ? "HALTED" : "OK");
   Print(statusLine);
   if(g_fileHandle != INVALID_HANDLE)
     {
      FileWrite(g_fileHandle, TimeToString(TimeCurrent()), g_symbol, "STATUS", statusLine);
      FileFlush(g_fileHandle);
     }
  }

//+------------------------------------------------------------------+
//| Writes a line to the human-readable .txt trade journal            |
//+------------------------------------------------------------------+
void JournalWrite(string event, string details)
  {
   if(g_journalHandle == INVALID_HANDLE) return;
   string line = TimeToString(TimeCurrent()) + "  | " + event + "  | " + details;
   FileWriteString(g_journalHandle, line + "\n");
   FileFlush(g_journalHandle);
  }

//+------------------------------------------------------------------+
//| One-shot journal hooks for the EA's OnTick (counter-diff based)  |
//+------------------------------------------------------------------+
void JournalCheckEvents(void)
  {
   // --- NEW ORDER(S) PLACED ---
   long placed = g_orderManager.GetOrdersPlaced();
   if(placed > g_lastPlacedCount)
     {
      g_lastPlacedCount = placed;
      JournalWrite("ORDER PLACED", "pending count now " + IntegerToString((int)placed));
     }

   // --- FILLED ---
   long filled = g_orderManager.GetOrdersFilled();
   if(filled > g_lastFilledCount)
     {
      g_lastFilledCount = filled;
      JournalWrite("FILLED", "");
     }

   // --- REJECTED ---
   long rejected = g_orderManager.GetOrdersRejected();
   if(rejected > g_lastRejectedCount)
     {
      g_lastRejectedCount = rejected;
      JournalWrite("REJECTED", "");
     }

   // --- TRADE MANAGER STEP CHANGES ---
   int halfRisk = g_tradeManager.GetHalfRiskTriggers();
   if(halfRisk > g_lastHalfRisk)
     { g_lastHalfRisk = halfRisk; JournalWrite("SL -> HALF-RISK", "risk now -0.5R"); }

   int be = g_tradeManager.GetBreakevenTriggers();
   if(be > g_lastBE)
     { g_lastBE = be; JournalWrite("SL -> BREAKEVEN (COST-COVERING)", ""); }

   int trail = g_tradeManager.GetTrailActivations();
   if(trail > g_lastTrail)
     { g_lastTrail = trail; JournalWrite("SL -> DYNAMIC TRAIL ACTIVE", ""); }

   // --- TRADE CLOSED (active->flat transition) ---
   // v5.30 FIX: re-arm the quorum latch on the flat transition. ArmQuorumGuard()
   // clears m_quorumFireLatched, which would otherwise only be cleared by the
   // constructor -- so the active-trade guard fired ONCE PER EA SESSION rather
   // than once per basket. Arming here means each new basket gets its own shot
   // at the once-per-basket contradiction cut.
   bool nowActive = g_orderManager.HasActiveTrade();
   if(g_lastHadTrade && !nowActive)
     {
      JournalWrite("TRADE CLOSED", "");
      g_tradeManager.ArmQuorumGuard();
     }
   g_lastHadTrade = nowActive;
  }

//+------------------------------------------------------------------+
//| OnTrade â€” re-sync active trade state on trade events             |
//+------------------------------------------------------------------+
void OnTrade(void)
  {
   if(g_initialized)
      g_orderManager.SyncActiveTrade();
  }
