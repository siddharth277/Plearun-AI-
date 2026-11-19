#!/usr/bin/env python3
"""Usage: python3 plearun_cli.py REPORT RECORDS_DIR [-v] > findings.json"""
import json
import sys

from plearun.loaders import load_records, read_text
from plearun.matcher import run


def main(argv):
    args = [a for a in argv[1:] if a != "-v"]
    verbose = "-v" in argv
    if len(args) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    report_text = read_text(args[0])
    rows, _problems = load_records(args[1])
    findings, reasons, ctx = run(report_text, rows)
    if verbose:
        for line in ctx.alignment.log:
            print("clock:", line, file=sys.stderr)
        for f, why in zip(findings, reasons):
            print("%-13s %s  [%s]" % (f["finding"], f["claim"][:70], why), file=sys.stderr)
    json.dump({"findings": findings}, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
