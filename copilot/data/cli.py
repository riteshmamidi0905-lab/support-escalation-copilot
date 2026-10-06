"""python -m copilot.data.cli generate --seed 20260101 --out data/meridian-seed-20260101   |   verify <dir> | hashes <dir>"""
import argparse
import json
import sys
from pathlib import Path

from copilot import contracts as C
from copilot.data import design, generator


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="copilot.data")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate")
    g.add_argument("--seed", type=int, default=generator.DEFAULT_SEED)
    g.add_argument("--out", type=Path, required=True)
    v = sub.add_parser("verify")
    v.add_argument("dir", type=Path)
    a = ap.parse_args(argv)
    if a.cmd == "generate":
        print(json.dumps(generator.write_dataset(a.seed, a.out), indent=1))
        return 0
    rep = C.validate_dataset(a.dir)
    d = design.design_report(a.dir)
    for e in rep.errors + d.errors:
        print("ERROR", e)
    print("OK" if rep.ok and d.ok else "FAILED")
    return 0 if rep.ok and d.ok else 1


if __name__ == "__main__":
    sys.exit(main())
