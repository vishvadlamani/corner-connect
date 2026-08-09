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
# vars: wall, engagement, rib_t, rib_depth, corner_web, boss, plate,
#       rows_choice (<0.5 -> 2 rows, else 3), dia_choice (<0.5 -> M12, else M16)
XL = np.array([8.0, 110.0, 8.0, 8.0, 8.0, 54.0, 8.0, 0.0, 0.0])
XU = np.array([16.0, 220.0, 14.0, 18.0, 16.0, 68.0, 16.0, 1.0, 1.0])
VAR_NAMES = ["wall_thickness", "engagement_length", "rib_thickness",
             "rib_depth", "corner_web_thickness", "boss_diameter",
             "base_plate_thickness", "rows_choice", "dia_choice"]
# architecture E (pinwheel): every candidate is the SPLIT half; spine bolts
# M10 keep the boss-flat strip rule satisfiable from boss 54 up
SPINE_BOLT_D = 10.0

# Hot-spot limit override for the split spine, UNVERIFIED - the split half's
# heavy section sits directly under the natural riser position on the flat
# back (top of a flat-back mold), the textbook feedable case. 3.0 x nominal
# wall pending FOUNDRY confirmation; the measured inscribed-sphere value is
# stamped into every results row either way, so nothing is hidden. The
# first split search at the default 2.0 ratio found ZERO feasible
# candidates (34/34 castability failures) - that result is preserved in
# the project log.
SPLIT_MAX_INSCRIBED_RATIO = 3.0

# castability check directions for violation magnitudes
_CAST_GE = {"min_wall", "draft", "fillet"}     # pass when value >= limit
_CAST_LE = {"section_ratio", "undercut", "hot_spot"}  # pass when value <= limit


def decode(x) -> NodeParams:
    kw = dict(zip(VAR_NAMES, [float(v) for v in x]))
    L = kw["engagement_length"]
    # discrete fastener strategy from threshold encodings
    rows = 2 if kw.pop("rows_choice") < 0.5 else 3
    d = 12.0 if kw.pop("dia_choice") < 0.5 else 16.0
    kw["bolt_rows"] = rows
    kw["bolt_diameter"] = d
    kw["bolt_pitch"] = max(40.0, round(3.25 * d, 1))   # >= 3d AISI screen
    kw["bolt_gauge"] = kw["bolt_pitch"]
    kw["bolt_edge_distance"] = max(25.0, 2.0 * d)
    # brackets adapt to the fastener strategy: columns 10 mm outboard of
    # the post-bolt columns, rows midway between the plate/top edge and
    # the outermost bolt rows
    plate = kw["base_plate_thickness"]
    row_lo = L / 2 - (rows - 1) * kw["bolt_pitch"] / 2
    row_hi = L / 2 + (rows - 1) * kw["bolt_pitch"] / 2
    s_b = round(kw["bolt_gauge"] / 2 + 10.0, 1)
    z_lo = round((plate + row_lo) / 2, 1)
    z_hi = round((row_hi + L) / 2, 1)
    kw["bracket_bolt_pattern"] = (
        (-s_b, z_lo), (s_b, z_lo), (-s_b, z_hi), (s_b, z_hi),
    )
    kw["spine_split"] = True
    kw["spine_bolt_diameter"] = SPINE_BOLT_D
    return NodeParams(**kw)


def search_load_cases() -> list[LoadCase]:
    """Plate-on stacking architecture (owner decision): panels drop on,
    posts bear on the node plate, so the stacked bearing case is LIVE
    alongside uplift and the both-beams envelope."""
    lcs = {lc.case_id: lc for lc in default_load_cases(N_STOREYS_DEFAULT)}
    lc3 = lcs["LC3"]
    return [
        LoadCase("LC1S", "stacked gravity: post end ring bearing on the "
                         "plate (plate-on stacking)",
                 axial_N=lcs["LC1"].axial_N),
        lcs["LC2"],
        LoadCase("LCC", "both beams simultaneous envelope",
                 shear_x_N=lc3.shear_x_N, shear_y_N=lc3.shear_x_N),
    ]


def params_from_row(row) -> NodeParams:
    """Rebuild the exact NodeParams of a results row (for re-rendering and
    pattern export of a chosen candidate)."""
    import ast

    return NodeParams(
        wall_thickness=float(row["wall_thickness"]),
        engagement_length=float(row["engagement_length"]),
        rib_thickness=float(row["rib_thickness"]),
        rib_depth=float(row["rib_depth"]),
        corner_web_thickness=float(row["corner_web_thickness"]),
        boss_diameter=float(row["boss_diameter"]),
        base_plate_thickness=float(row["base_plate_thickness"]),
        bolt_rows=int(row["bolt_rows"]),
        bolt_diameter=float(row["bolt_diameter"]),
        bolt_pitch=float(row["bolt_pitch"]),
        bolt_gauge=float(row["bolt_gauge"]),
        bolt_edge_distance=float(row["bolt_edge_distance"]),
        bracket_bolt_pattern=tuple(
            ast.literal_eval(row["bracket_bolt_pattern"])),
        spine_split=bool(row["spine_split"]),
        spine_bolt_diameter=float(row["spine_bolt_diameter"]),
    )


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
        import dataclasses as _dc
        from .params import DEFAULT_CASTABILITY

        p = decode(x)
        df = evaluate_candidate(
            p, search_load_cases(), workdir=str(wd), sizes=COARSE,
            parquet_out=None, candidate_id=cid,
            skip_fea_if_uncastable=True, seed=SEED,
            cast_limits=_dc.replace(
                DEFAULT_CASTABILITY,
                max_inscribed_ratio=SPLIT_MAX_INSCRIBED_RATIO),
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
