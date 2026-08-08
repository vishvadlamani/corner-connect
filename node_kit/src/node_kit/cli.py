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


def cmd_evaluate(out_dir: str, coarse: bool, storeys: int) -> int:
    from .params import default_load_cases
    from .objective import evaluate_candidate, COARSE

    df = evaluate_candidate(
        NodeParams(),
        default_load_cases(n_storeys=storeys),
        workdir=f"{out_dir}/candidate_default",
        sizes=COARSE if coarse else None,
        parquet_out=f"{out_dir}/results.parquet",
    )
    cols = ["load_case", "casting_peak_vm_MPa", "post_wall_peak_vm_MPa",
            "joint_stiffness_N_mm", "ratio_bearing_J332", "ratio_bolt_shear",
            "ratio_casting_vm", "governing_limit_state", "lc_pass"]
    print(df[cols].to_string(index=False))
    print(f"\nmass: {df['mass_kg'].iloc[0]:.2f} kg   "
          f"candidate_pass: {df['candidate_pass'].iloc[0]}")
    print(f"parquet: {out_dir}/results.parquet")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="node_kit")
    sub = parser.add_subparsers(dest="command", required=True)
    exp = sub.add_parser("export", help="export default node STEP + STL")
    exp.add_argument("--out", default="runs", help="output directory")
    ev = sub.add_parser("evaluate",
                        help="evaluate the default candidate end to end")
    ev.add_argument("--out", default="runs", help="output directory")
    ev.add_argument("--coarse", action="store_true", help="coarse meshes")
    ev.add_argument("--storeys", type=int, default=5)
    args = parser.parse_args(argv)
    if args.command == "export":
        return cmd_export(args.out)
    if args.command == "evaluate":
        return cmd_evaluate(args.out, args.coarse, args.storeys)
    return 2


if __name__ == "__main__":
    sys.exit(main())
