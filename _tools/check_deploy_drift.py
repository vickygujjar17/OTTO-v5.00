#!/usr/bin/env python3
"""
check_deploy_drift.py -- OTTO EA deployed-header drift audit.

Angle-bracket includes (<Otto/*.mqh>) in otto.mq5 resolve against the LIVE
terminal's MQL5\\Include\\Otto folder, NOT against the repo or the staging
tree. build_check.ps1 no longer mirrors headers there, so this script is the
only way to prove whether a successful-looking compile actually linked the
CURRENT repo headers or a stale/corrupted deployed copy.

Compares, per file:
  * repo HEAD blob            (git show HEAD:<file>)
  * repo worktree file
  * deployed terminal copy

Read-only: never writes anything.
Exit 0 = deployed copies match the repo worktree content.
Exit 1 = drift detected.
"""

import os
import subprocess
import sys

EXTS = (".mq5", ".mqh")

TERMINAL = os.path.join(
    os.environ.get("APPDATA", ""),
    "MetaQuotes", "Terminal",
    "10CE948A1DFC9A8C27E56E827008EBD4",
    "MQL5")
INC_OTTO = os.path.join(TERMINAL, "Include", "Otto")
EXPERTS = os.path.join(TERMINAL, "Experts")


def deployed_path(base):
    """Map a repo file to the terminal location it is deployed to.

    Layout matters: the OTTO modules (.mqh) resolve through the angle-bracket
    include <Otto/...>, so they live in MQL5\\Include\\Otto. The entry point
    (.mq5) is an EA and lives in MQL5\\Experts. Looking for otto.mq5 under
    Include\\Otto reported a permanent false "NOT DEPLOYED".
    """
    if base.lower().endswith(".mq5"):
        return os.path.join(EXPERTS, base)
    return os.path.join(INC_OTTO, base)


def norm(data):
    return data.replace(b"\r\n", b"\n")


def nonascii(data):
    return sum(1 for b in data if b > 127)


def read_or_none(path):
    if not path or not os.path.isfile(path):
        return None
    with open(path, "rb") as fh:
        return fh.read()


def main():
    repo = sys.argv[1] if len(sys.argv) > 1 else "."
    os.chdir(repo)

    print("=" * 104)
    print("OTTO DEPLOYED-HEADER DRIFT AUDIT")
    print("=" * 104)
    print("repo    : %s" % os.getcwd())
    print("modules : %s" % INC_OTTO)
    print("experts : %s" % EXPERTS)
    print("modules dir exists: %s" % os.path.isdir(INC_OTTO))
    print("")
    print("%-28s %9s %9s %9s  %s"
          % ("FILE", "HEADna", "WORKna", "DEPLna", "VERDICT"))
    print("-" * 104)

    paths = sorted(f for f in os.listdir(".") if f.endswith(EXTS))
    if not paths:
        print("no .mq5/.mqh files in repo root")
        return 2

    drift = 0
    for base in paths:
        head = subprocess.run(["git", "show", "HEAD:" + base],
                              capture_output=True).stdout or None
        work = read_or_none(base)
        depl = read_or_none(deployed_path(base))

        if depl is None:
            verdict = "NOT DEPLOYED"
            drift += 1
        elif work is not None and norm(depl) == norm(work):
            verdict = "in sync with repo"
        elif head is not None and norm(depl) == norm(head):
            verdict = "matches HEAD (repo worktree differs)"
        else:
            verdict = "*** DRIFT ***"
            drift += 1

        print("%-28s %9s %9s %9s  %s"
              % (base,
                 nonascii(head) if head else "-",
                 nonascii(work) if work else "-",
                 nonascii(depl) if depl else "-",
                 verdict))

    # --- Shadow-header scan ------------------------------------------------
    # Angle-bracket includes are resolved by RECURSIVE FILE-NAME search over
    # MQL5\Include, so any second copy of an OTTO module under that tree is a
    # competing candidate for <Otto/X.mqh>. On 24-09-2026 a loose v4.80
    # COttoOrderManager.mqh at Include root out-resolved the real v5.31 header
    # and produced "'BeginOrderCycle' - undeclared identifier" at otto.mq5:730.
    # A module can be perfectly "in sync" in Include\Otto and still lose the
    # link, so this must be checked separately.
    #
    # Scope is INCLUDE only -- that is the search path. The quarantine folder
    # MQL5\_legacy_unused is deliberately OUTSIDE it and must NOT be reported;
    # flagging it would make this gate a permanent false red.
    inc_root = os.path.join(TERMINAL, "Include")
    shadows = []
    if os.path.isdir(INC_OTTO):
        mods = {f.lower() for f in os.listdir(INC_OTTO) if f.lower().endswith(".mqh")}
        for root, _dirs, files in os.walk(inc_root):
            if root.lower() == INC_OTTO.lower():
                continue
            for f in files:
                if f.lower() in mods:
                    rel = os.path.relpath(os.path.join(root, f), inc_root)
                    shadows.append(rel)

    print()
    print("-" * 104)
    print("SHADOW-HEADER SCAN (competing copies of OTTO modules under MQL5\\Include)")
    print("-" * 104)
    if shadows:
        for s in sorted(set(shadows)):
            print("  *** SHADOW: Include\\%s" % s)
        print("  a legacy copy can out-resolve Include\\Otto -> undeclared identifier")
    else:
        print("  none -- Include\\Otto is the only candidate for every OTTO module")

    print()
    print("-" * 104)
    print("files=%d  drifted=%d  shadows=%d" % (len(paths), drift, len(set(shadows))))
    if drift == 0 and not shadows:
        print("*** DEPLOY IN SYNC ***")
        return 0
    print("*** DRIFT PRESENT: compile gate would link stale/corrupt headers ***")
    return 1


if __name__ == "__main__":
    sys.exit(main())