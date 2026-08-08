"""Command-line entry points. Everything in the pipeline is runnable headless.

Usage:
    python -m node_kit.cli export [--out DIR]      # default node STEP + STL
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

from .params import NodeParams
from .geometry import build_node, export_step, export_stl, node_summary


def cmd_export(out_dir: str) -> int:
    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    p = NodeParams()
    result = build_node(p)
    step_path = export_step(result, str(out / "node_default.step"))
    stl_path = export_stl(result, str(out / "node_default.stl"))
    summary = node_summary(p, result)
    (out / "node_default_summary.json").write_text(
        json.dumps(summary, indent=2, default=str)
    )
    print(f"STEP: {step_path}")
    print(f"STL:  {stl_path}")
    print(json.dumps(summary, indent=2, default=str))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="node_kit")
    sub = parser.add_subparsers(dest="command", required=True)
    exp = sub.add_parser("export", help="export default node STEP + STL")
    exp.add_argument("--out", default="runs", help="output directory")
    args = parser.parse_args(argv)
    if args.command == "export":
        return cmd_export(args.out)
    return 2


if __name__ == "__main__":
    sys.exit(main())
