"""v5.32 maintenance: roll the older probes onto the new release.

Anchors are single-line substrings wherever possible so the mixed LF/CRLF
terminators inside the probe suite cannot cause a mismatch; the two places
that must ADD a line use an explicit CRLF join. Each anchor must match
exactly once. Byte discipline preserved with newline="".
"""

import io
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

EDITS = {
    "_probe/test_v527_quorum.py": [
        ('check("input InpEnableQuorumGuard defaults true",',
         'check("input InpEnableQuorumGuard defaults false (v5.32 rollback)",'),
        (" 12. Version stamp 5.31 present.", " 12. Version stamp 5.32 present."),
    ],
    "_probe/test_v527_exit_comment.py": [
        ("14. Version stamp 5.30 present; no 5.27/5.28/5.29 property stamp survives.",
         "14. Version stamp 5.32 present; no 5.27/5.28/5.29/5.30/5.31 property stamp survives."),
        ("# The 10 files that must all carry the 5.31 stamp.",
         "# The 10 files that must all carry the 5.32 stamp."),
        ("if '#property version   \"5.31\"' not in read(os.path.join(ROOT, f))]",
         "if '#property version   \"5.32\"' not in read(os.path.join(ROOT, f))]"),
        ('check("all 10 files stamp 5.31"', 'check("all 10 files stamp 5.32"'),
        ("or '#property version   \"5.30\"' in read(os.path.join(ROOT, f))]",
         "or '#property version   \"5.30\"' in read(os.path.join(ROOT, f))\r\n"
         "         or '#property version   \"5.31\"' in read(os.path.join(ROOT, f))]"),
        ('check("no 5.27/5.28/5.29/5.30 property stamp survives"',
         'check("no 5.27/5.28/5.29/5.30/5.31 property stamp survives"'),
        ('"OTTO EA v5.31" in DEFS_T)', '"OTTO EA v5.32" in DEFS_T)'),
        ("'#property description \"OTTO v5.31' in DEFS_T)",
         "'#property description \"OTTO v5.32' in DEFS_T)"),
    ],
    "_probe/test_v529_floatingloss.py": [
        ('#property version   "5.31"\' in txt)', '#property version   "5.32"\' in txt)'),
        ('check("5.31 stamped in %s" % f,', 'check("5.32 stamped in %s" % f,'),
        ('check("no 5.28/5.29/5.30 property stamp in %s" % f,',
         'check("no 5.28/5.29/5.30/5.31 property stamp in %s" % f,'),
        ("'#property version   \"5.30\"' not in txt)",
         "'#property version   \"5.30\"' not in txt\r\n"
         "          and '#property version   \"5.31\"' not in txt)"),
        ('check("OttoDefines banner names v5.31", "OTTO EA v5.31" in DEFS_T)',
         'check("OttoDefines banner names v5.32", "OTTO EA v5.32" in DEFS_T)'),
        ('check("OttoDefines description names v5.31",',
         'check("OttoDefines description names v5.32",'),
        ("'#property description \"OTTO v5.31' in DEFS_T)",
         "'#property description \"OTTO v5.32' in DEFS_T)"),
        ('check("otto.mq5 port banner reads v5.31",',
         'check("otto.mq5 port banner reads v5.32",'),
        ('"Pine Script Master Build Port (v5.31)" in MQ5_T)',
         '"Pine Script Master Build Port (v5.32)" in MQ5_T)'),
        ('check("otto.mq5 init banner reads v5.31", "OTTO EA v5.31" in MQ5_T)',
         'check("otto.mq5 init banner reads v5.32", "OTTO EA v5.32" in MQ5_T)'),
    ],
    "_probe/test_v531_double_delete.py": [
        ("7. Version stamp 5.31 present in all 10 files; 5.30 rejected.",
         "7. Version stamp 5.32 present in all 10 files; 5.30/5.31 rejected."),
        ('check("5.31 stamped in %s" % f,', 'check("5.32 stamped in %s" % f,'),
        ('#property version   "5.31"\' in txt)', '#property version   "5.32"\' in txt)'),
        ('check("no 5.30 property stamp in %s" % f,',
         'check("no 5.30/5.31 property stamp in %s" % f,'),
        ("'#property version   \"5.30\"' not in txt)",
         "'#property version   \"5.30\"' not in txt\r\n"
         "          and '#property version   \"5.31\"' not in txt)"),
        ('check("OttoDefines banner names v5.31", "OTTO EA v5.31" in DEFS_T)',
         'check("OttoDefines banner names v5.32", "OTTO EA v5.32" in DEFS_T)'),
        ('check("OttoDefines description names v5.31",',
         'check("OttoDefines description names v5.32",'),
        ("'#property description \"OTTO v5.31' in DEFS_T)",
         "'#property description \"OTTO v5.32' in DEFS_T)"),
        ('check("otto.mq5 port banner reads v5.31",',
         'check("otto.mq5 port banner reads v5.32",'),
        ('"Pine Script Master Build Port (v5.31)" in MQ5_T)',
         '"Pine Script Master Build Port (v5.32)" in MQ5_T)'),
        ('check("otto.mq5 init banner reads v5.31", "OTTO EA v5.31" in MQ5_T)',
         'check("otto.mq5 init banner reads v5.32", "OTTO EA v5.32" in MQ5_T)'),
    ],
}


def main():
    total = 0
    for rel, edits in EDITS.items():
        path = os.path.join(ROOT, rel.replace("/", os.sep))
        text = io.open(path, encoding="utf-8", newline="").read()
        for old, new in edits:
            n = text.count(old)
            if n != 1:
                raise SystemExit("ANCHOR ERROR [%s]: %d matches for: %r"
                                 % (rel, n, old[:60]))
            text = text.replace(old, new, 1)
            total += 1
        io.open(path, "w", encoding="utf-8", newline="").write(text)
        print("  ok  %-40s %d edit(s)" % (rel, len(edits)))
    print("\napplied %d edit(s)" % total)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
