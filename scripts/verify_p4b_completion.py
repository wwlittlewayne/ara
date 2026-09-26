#!/usr/bin/env python3
"""Verify completed P4B runs, including explicitly rerun cycle-limit cases."""
import re
import sys
from pathlib import Path


def ledger(directory):
    rows = {}
    for line in (directory / "summary.log").read_text().splitlines():
        fields = line.split()
        if len(fields) < 3 or fields[0] not in ("v128", "v256"):
            raise ValueError(f"Unexpected ledger line: {line}")
        key = tuple(fields[:2])
        if key in rows:
            raise ValueError(f"Duplicate ledger entry: {key}")
        rows[key] = fields[2]
    return rows


def main():
    full, extended = map(Path, sys.argv[1:])
    rows, reruns = ledger(full), ledger(extended)
    expected = {(tag, f"case-{i:03}") for tag in ("v128", "v256") for i in range(148)}
    assert set(rows) == expected, "Full ledger must cover exactly 296 cases"
    assert set(reruns) == {(tag, "case-146") for tag in ("v128", "v256")}
    errors = re.compile(r"P4B_MISMATCH|P4B_TRAP|P4B_MASK_FAILURE|%Error|Assertion|"
                        r"\[(?:DISP|MASKU)-A\d|Simulation timeout|\*\*\* FAILED")
    for tag, case in sorted(expected):
        directory = extended if (tag, case) in reruns else full
        status = reruns.get((tag, case), rows[(tag, case)])
        log = directory / f"{tag}_{case}.log"
        text = log.read_text(errors="replace")
        assert status == "PASS", f"Runner failure: {log}"
        assert "P4B_BARE_PASS" in text and "complete=1" in text, f"Incomplete: {log}"
        assert "*** SUCCESS ***" in text, f"No successful tohost: {log}"
        assert not errors.search(text), f"Error/assertion/timeout: {log}"
    for tag in ("v128", "v256"):
        print(f"{tag}: 148/148 completed BARE_PASS; traps=0 mismatches=0 assertion_errors=0")
    print("case-146: completed reruns at 12000000-cycle limit used for both VLENs")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(f"usage: {sys.argv[0]} FULL_SUITE_DIR EXTENDED_CASE146_DIR")
    main()
