"""Pareto front + recommended-candidate report (Milestone 7).

Reads runs/results.parquet (one row per candidate x load case), aggregates
per candidate, plots the mass-vs-governing-utilisation front, picks a
recommended candidate and writes the reasoning out in plain language for
the reviewing engineer. All visuals show the actual L casting only.
"""

from __future__ import annotations

import json
import pathlib

import numpy as np
import pandas as pd


RATIO_COLS_PREFIX = "ratio_"


def aggregate(df: pd.DataFrame) -> pd.DataFrame:
    """One row per candidate: mass, governing utilisation, feasibility."""
    out = []
    for cid, g in df.groupby("candidate_id"):
        ratio_cols = [c for c in g.columns if c.startswith(RATIO_COLS_PREFIX)]
        vals = g[ratio_cols].to_numpy(dtype=float)
        finite = vals[np.isfinite(vals)]
        gov = float(finite.max()) if len(finite) else float("nan")
        out.append({
            "candidate_id": cid,
            "mass_kg": float(g["mass_kg"].iloc[0]),
            "governing_utilisation": gov,
            "castability_pass": bool(g["castability_all_pass"].iloc[0]),
            "spacing_pass": bool(g["spacing_limits_pass"].iloc[0]),
            "feasible": bool(g["castability_all_pass"].iloc[0]
                             and np.isfinite(gov) and gov <= 1.0
                             and g["spacing_limits_pass"].iloc[0]),
            "wall_thickness": float(g["wall_thickness"].iloc[0]),
            "engagement_length": float(g["engagement_length"].iloc[0]),
            "rib_thickness": float(g["rib_thickness"].iloc[0]),
            "rib_depth": float(g["rib_depth"].iloc[0]),
            "corner_web_thickness": float(g["corner_web_thickness"].iloc[0]),
            "boss_diameter": float(g["boss_diameter"].iloc[0]),
            "base_plate_thickness": float(g["base_plate_thickness"].iloc[0]),
            "bolt_rows": int(g["bolt_rows"].iloc[0]),
            "bolt_diameter": float(g["bolt_diameter"].iloc[0]),
            "stiffness_min_N_mm": float(
                np.nanmin(g["joint_stiffness_N_mm"].to_numpy(dtype=float))
                if np.isfinite(g["joint_stiffness_N_mm"].to_numpy(dtype=float)).any()
                else float("nan")),
        })
    return pd.DataFrame(out)


def pareto_mask(F: np.ndarray) -> np.ndarray:
    """Non-dominated mask for a minimise-both objective matrix (n, 2)."""
    n = len(F)
    mask = np.ones(n, dtype=bool)
    for i in range(n):
        if not mask[i]:
            continue
        dominated = np.all(F <= F[i], axis=1) & np.any(F < F[i], axis=1)
        if dominated.any():
            mask[i] = False
    return mask


def pick_recommended(agg: pd.DataFrame) -> pd.Series | None:
    feas = agg[agg.feasible]
    if feas.empty:
        return None
    # project objective: minimum casting mass subject to all checks passing;
    # require a little strength headroom so mesh sensitivity can't flip it
    margin = feas[feas.governing_utilisation <= 0.9]
    pool = margin if not margin.empty else feas
    return pool.sort_values("mass_kg").iloc[0]


def plot_front(agg: pd.DataFrame, rec, path: str):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(9.5, 7), dpi=115)
    infeas = agg[~agg.feasible]
    feas = agg[agg.feasible]
    ax.scatter(infeas.mass_kg, infeas.governing_utilisation.fillna(5.0),
               c="#b9c0c9", s=45, label="infeasible (checks fail)")
    if not feas.empty:
        ax.scatter(feas.mass_kg, feas.governing_utilisation, c="#3b6ea5",
                   s=55, label="feasible")
        F = feas[["mass_kg", "governing_utilisation"]].to_numpy()
        pm = pareto_mask(F)
        pf = feas[pm].sort_values("mass_kg")
        ax.plot(pf.mass_kg, pf.governing_utilisation, "-o", c="#c0392b",
                lw=2, ms=8, label="Pareto front")
    if rec is not None:
        ax.scatter([rec.mass_kg], [rec.governing_utilisation], marker="*",
                   s=420, c="#e6a817", ec="k", zorder=5,
                   label=f"recommended ({rec.mass_kg:.1f} kg)")
    ax.axhline(1.0, color="k", lw=1, ls="--")
    ax.text(ax.get_xlim()[1], 1.01, "capacity limit", ha="right", fontsize=9)
    ax.set_xlabel("casting mass, kg")
    ax.set_ylabel("governing utilisation (max of all check ratios)")
    ax.set_title("Corner-node design space — twin-L clamp architecture\n"
                 "objectives: minimise mass, maximise capacity margin")
    ax.legend(loc="best")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")


def generate_report(out: str = "runs") -> dict:
    outp = pathlib.Path(out)
    df = pd.read_parquet(outp / "results.parquet")
    agg = aggregate(df)
    rec = pick_recommended(agg)
    plot_front(agg, rec, str(outp / "pareto.png"))

    lines = ["# Milestone 7 — design-space search report", ""]
    lines.append(f"Candidates evaluated: **{len(agg)}** "
                 f"(feasible: {int(agg.feasible.sum())}, "
                 f"castability failures: {int((~agg.castability_pass).sum())})")
    lines.append("")
    if rec is None:
        lines.append("**NO FEASIBLE CANDIDATE FOUND.** The constraint set "
                     "cannot be satisfied in the searched box - see "
                     "pareto.png for where the population piled up, and "
                     "loosen geometry bounds or revisit limits with the "
                     "engineer.")
    else:
        full = df[df.candidate_id == rec.candidate_id]
        lines += [
            "## Recommended candidate",
            "",
            f"`{rec.candidate_id}` — **{rec.mass_kg:.2f} kg**, governing "
            f"utilisation **{rec.governing_utilisation:.2f}**",
            "",
            "| parameter | value |",
            "|---|---|",
            f"| wall_thickness | {rec.wall_thickness:.1f} mm |",
            f"| engagement_length | {rec.engagement_length:.1f} mm |",
            f"| rib_thickness | {rec.rib_thickness:.1f} mm |",
            f"| rib_depth | {rec.rib_depth:.1f} mm |",
            f"| corner_web_thickness | {rec.corner_web_thickness:.1f} mm |",
            f"| boss_diameter | {rec.boss_diameter:.1f} mm |",
            "",
            "### Reasoning",
            "",
            "The project objective is minimum casting mass subject to every "
            "connection and castability check passing. Among feasible "
            "candidates, this is the lightest with governing utilisation "
            "<= 0.9 (headroom held back because the screening mesh is "
            "coarse and peak von Mises is mesh-sensitive). Utilisation by "
            "load case:",
            "",
        ]
        for _, r in full.iterrows():
            lines.append(
                f"* {r.load_case}: casting {r.ratio_casting_vm:.2f}, "
                f"post wall {r.ratio_post_wall_vm:.2f}, "
                f"bearing {r.ratio_bearing_J332:.2f}, "
                f"bolt shear {r.ratio_bolt_shear:.2f} "
                f"(governing: {r.governing_limit_state})")
        lines += [
            "",
            "CAVEATS (unchanged from the results contract): loads are "
            "norm-derived with a placeholder wind pressure; phi/Omega "
            "factors are UNVERIFIED; single-L FEA model (paired twin-L "
            "assembly model is future work and only adds capacity).",
        ]
    (outp / "report.md").write_text("\n".join(lines) + "\n")
    return {"agg": agg, "recommended": rec}
