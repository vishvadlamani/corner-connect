"""NSGA-II design-space search (Milestone 7). Deterministic, headless.

ARCHITECTURE BASIS (owner decision, 2026-08): twin-L split collar on a
CONTINUOUS post ("clamp"). Consequences for the optimisation load set:
accumulated gravity stays in the post, so the stacking cases (LC1, and
LC5's axial part) never load the casting and are omitted here. The casting
is optimised against:
    LC2  rod uplift (86 kN norm-derived, wind placeholder), and
    LCC  both beams loaded simultaneously at the floor-beam reaction
         (symmetric envelope of LC3/LC4/LC5-shear).
The floor-socket variant (base levels) re-introduces bearing cases and is
NOT part of this search.

Design variables (6, continuous; everything else fixed at kit defaults):
    wall_thickness        [8, 16] mm
    engagement_length     [140, 220] mm
    rib_thickness         [8, 14] mm
    rib_depth             [8, 18] mm
    corner_web_thickness  [8, 16] mm
    boss_diameter         [40, 58] mm
The bracket bolt pattern scales with engagement (rows at 0.3L / 0.7L).

Objectives:  minimise casting mass;  minimise governing utilisation
(= maximise the minimum capacity margin).
Constraints: governing utilisation <= 1 (all AISI ratios and von Mises
screens); all castability checks pass; parameter-validity built in.
Candidates failing castability skip FEA (feasibility-first NSGA-II ranks
them by violation; their strength is never reported as known).

Every evaluation appends full contract rows; the search writes
runs/results.parquet and hands over to report.py.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys

import numpy as np
import pandas as pd

from .params import NodeParams, LoadCase, default_load_cases, N_STOREYS_DEFAULT

SEED = 42
XL = np.array([8.0, 140.0, 8.0, 8.0, 8.0, 40.0])
XU = np.array([16.0, 220.0, 14.0, 18.0, 16.0, 58.0])
VAR_NAMES = ["wall_thickness", "engagement_length", "rib_thickness",
             "rib_depth", "corner_web_thickness", "boss_diameter"]

# castability check directions for violation magnitudes
_CAST_GE = {"min_wall", "draft", "fillet"}     # pass when value >= limit
_CAST_LE = {"section_ratio", "undercut", "hot_spot"}  # pass when value <= limit


def decode(x) -> NodeParams:
    kw = dict(zip(VAR_NAMES, [float(v) for v in x]))
    L = kw["engagement_length"]
    kw["bracket_bolt_pattern"] = (
        (-38.0, round(0.3 * L, 1)), (38.0, round(0.3 * L, 1)),
        (-38.0, round(0.7 * L, 1)), (38.0, round(0.7 * L, 1)),
    )
    return NodeParams(**kw)


def search_load_cases() -> list[LoadCase]:
    lcs = {lc.case_id: lc for lc in default_load_cases(N_STOREYS_DEFAULT)}
    lc3 = lcs["LC3"]
    return [
        lcs["LC2"],
        LoadCase("LCC", "both beams simultaneous (clamp-architecture envelope)",
                 shear_x_N=lc3.shear_x_N, shear_y_N=lc3.shear_x_N),
    ]


def candidate_id_for(x) -> str:
    return hashlib.sha1(np.round(np.asarray(x, float), 4).tobytes()).hexdigest()[:10]


def cast_violation(row: pd.Series) -> float:
    """Max normalized castability violation over all checks (<= 0 feasible)."""
    worst = -1.0
    for name in list(_CAST_GE) + list(_CAST_LE):
        v, lim = row.get(f"cast_{name}_value"), row.get(f"cast_{name}_limit")
        if v is None or lim is None or not np.isfinite(v):
            continue
        if name in _CAST_GE:
            viol = (lim - v) / max(lim, 1e-9)
        else:
            viol = (v - lim) / max(lim, 1e-9)
        worst = max(worst, viol)
    return worst


def strength_ratio(df: pd.DataFrame) -> float:
    cols = [c for c in df.columns if c.startswith("ratio_")]
    vals = df[cols].to_numpy(dtype=float)
    vals = vals[np.isfinite(vals)]
    return float(vals.max()) if len(vals) else float("nan")


def evaluate_x(x, out_root: str) -> dict:
    """One candidate: returns {'F': [...], 'G': [...]} and writes contract
    rows to <out_root>/cand_<id>/rows.parquet. Never raises."""
    from .objective import evaluate_candidate, COARSE

    cid = candidate_id_for(x)
    wd = pathlib.Path(out_root) / f"cand_{cid}"
    try:
        p = decode(x)
        df = evaluate_candidate(
            p, search_load_cases(), workdir=str(wd), sizes=COARSE,
            parquet_out=None, candidate_id=cid,
            skip_fea_if_uncastable=True, seed=SEED,
        )
    except Exception as exc:
        wd.mkdir(parents=True, exist_ok=True)
        (wd / "invalid.json").write_text(json.dumps(
            {"x": list(map(float, x)), "error": repr(exc)[:500]}))
        return {"F": [1e3, 1e3], "G": [10.0, 10.0]}

    df.to_parquet(wd / "rows.parquet", index=False)
    mass = float(df["mass_kg"].iloc[0])
    g_cast = cast_violation(df.iloc[0])
    ratio = strength_ratio(df)
    if not np.isfinite(ratio):          # FEA skipped (uncastable)
        return {"F": [mass, 5.0], "G": [0.0, g_cast]}
    g_spacing = 0.0 if bool(df["spacing_limits_pass"].iloc[0]) else 10.0
    return {"F": [mass, ratio], "G": [ratio - 1.0 + g_spacing, g_cast]}


from pymoo.core.problem import ElementwiseProblem


class NodeProblem(ElementwiseProblem):
    """Module-level so multiprocessing can pickle it."""

    def __init__(self, out_root: str = "runs/search", **kwargs):
        self.out_root = out_root
        super().__init__(n_var=len(XL), n_obj=2, n_ieq_constr=2,
                         xl=XL, xu=XU, **kwargs)

    def _evaluate(self, x, out_dict, *args, **kwargs):
        res = evaluate_x(x, self.out_root)
        out_dict["F"] = res["F"]
        out_dict["G"] = res["G"]


def run_search(pop: int = 10, gen: int = 6, out: str = "runs",
               n_proc: int = 2, seed: int = SEED) -> pd.DataFrame:
    from multiprocessing import Pool
    from pymoo.algorithms.moo.nsga2 import NSGA2
    from pymoo.core.problem import StarmapParallelization
    from pymoo.optimize import minimize

    out_root = str(pathlib.Path(out) / "search")
    pathlib.Path(out_root).mkdir(parents=True, exist_ok=True)

    with Pool(n_proc) as pool:
        problem = NodeProblem(
            out_root=out_root,
            elementwise_runner=StarmapParallelization(pool.starmap))
        algo = NSGA2(pop_size=pop)
        res = minimize(problem, algo, ("n_gen", gen), seed=seed,
                       verbose=True, save_history=False)

    # collect every evaluated candidate's contract rows
    frames = []
    for rows in sorted(pathlib.Path(out_root).glob("cand_*/rows.parquet")):
        frames.append(pd.read_parquet(rows))
    all_rows = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    parquet = pathlib.Path(out) / "results.parquet"
    all_rows.to_parquet(parquet, index=False)

    summary = {
        "n_evaluated": len(frames),
        "seed": seed, "pop": pop, "gen": gen,
        "pareto_X": [list(map(float, r)) for r in np.atleast_2d(res.X)] if res.X is not None else [],
        "pareto_F": [list(map(float, r)) for r in np.atleast_2d(res.F)] if res.F is not None else [],
        "var_names": VAR_NAMES,
    }
    (pathlib.Path(out) / "search_summary.json").write_text(
        json.dumps(summary, indent=2))
    return all_rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="node_kit.search")
    ap.add_argument("--pop", type=int, default=10)
    ap.add_argument("--gen", type=int, default=6)
    ap.add_argument("--out", default="runs")
    ap.add_argument("--procs", type=int, default=2)
    ap.add_argument("--seed", type=int, default=SEED)
    args = ap.parse_args(argv)
    run_search(args.pop, args.gen, args.out, args.procs, args.seed)
    from .report import generate_report
    generate_report(args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
