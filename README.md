# OTTO EA

MetaTrader 5 Expert Advisor — MQL5 port of the Pine Script `prop_guard_tester.pine`
master build. **Current base: v5.31**

## Layout

| File | Role |
|---|---|
| `OttoDefines.mqh` | Central enums, structs and `input` parameters |
| `COttoNewsFilter.mqh` | News shield |
| `COttoRiskManager.mqh` | Position sizing / risk |
| `COttoMarketStructure.mqh` | Market-day counter, structure helpers |
| `COttoBlockManager.mqh` | Wick1+Wick2 S/R block lifecycle and vetoes |
| `COttoOrderManager.mqh` | Order placement, pyramiding basket, journal hooks |
| `COttoCorrelationFilter.mqh` | Cross-symbol correlation veto |
| `COttoTradeManager.mqh` | Cut / cost-BE / lock3 / ATR trail / pyramiding |
| `COttoJournal.mqh` | Human-readable trade journal |
| `otto.mq5` | EA entry point and event handlers |

## Build (strict gate)

Sources are CRLF and the EA resolves its modules through angle-bracket includes
(`#include <Otto/<file>.mqh>`), so it is compiled from a staged tree that mirrors
the terminal layout:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File _tools\build_check.ps1
```

The gate is only passed on **0 errors, 0 warnings**.

## Deployment (why the gate alone is not enough)

`build_check.ps1` passes `/inc:<stage>` to MetaEditor, so it **never reads the
terminal's `MQL5\Include`**. A manual F7 compile always does. This matters because
angle-bracket includes are resolved by a **recursive file-name search** of the
live terminal's include tree: a stale header anywhere under `MQL5\Include` can
out-resolve the correct module and produce a bogus compile error in a source file
that is actually correct.

That is exactly what happened at v5.31: a loose v4.80 `COttoOrderManager.mqh` at
`MQL5\Include` root was linked instead of the v5.31 module, and the editor reported

```
otto.mq5(730) : error: 'BeginOrderCycle' - undeclared identifier
otto.mq5(730) : error: ')' - expression expected
```

even though `BeginOrderCycle()` is implemented in `COttoOrderManager.mqh` and the
staged gate was green.

Deploy, then audit:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File _tools\deploy_to_terminal.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File _tools\deploy_to_terminal.ps1 -DryRun
python _tools\check_deploy_drift.py .
```

`deploy_to_terminal.ps1` mirrors the modules to `MQL5\Include\Otto\`, copies
`otto.mq5` to `MQL5\Experts\`, and moves every shadow copy OUT of `MQL5\Include`
into `MQL5\_legacy_unused\` (moves only — nothing is deleted, so it is reversible).
The quarantine destination is deliberately outside `Include\`, because a folder
inside it would still be searched recursively and the ambiguity would survive.

After deploying, the audit must print `*** DEPLOY IN SYNC ***` with `shadows=0`.
That, plus a manual F7 compile, is the real proof — not the gate.

## Line endings

`_tools\normalize_eol.py` rewrites MQL5 sources to strict CRLF byte-safely
(non-ASCII content is preserved exactly):

```powershell
python _tools\normalize_eol.py .            # rewrite
python _tools\normalize_eol.py . --check    # report only
```

Other checks:

```powershell
python _tools\verify_integrity.py .                    # sources vs HEAD blob + CRLF
python _tools\repair_mojibake.py . --check             # encoding
python _probe\test_v531_double_delete.py               # + 12 more in _probe\
```

## Order-deletion design note

v5.31 deletes orders through a single funnel, `SafeDeleteOrder()`, backed by the
per-cycle ledger `m_deletedThisCycle[]` / `BeginOrderCycle()`. All same-tick
deletion sites route through it so a ticket cannot be dispatched twice in one
cycle (the source of the `[Invalid request] buy 0` rejection).

Do **not** replace this with an `OrderSelect()` / `ORDER_STATE_PLACED`
re-validation. MT5 does not refresh its order pool inside a single event handler,
so such a check reads the same stale cache that permits the duplicate
`TRADE_ACTION_REMOVE` — it is a no-op that only looks safer.

## Version policy

Every update bumps `#property version` in **all** `.mqh`/`.mq5` files, the
banner in `OttoDefines.mqh`, and the startup `Print()` banner in `otto.mq5`.