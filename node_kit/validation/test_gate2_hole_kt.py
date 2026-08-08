"""VALIDATION GATE 2: stress concentration at a circular hole, Kt = 3.0.

Infinite plate in uniaxial tension has Kt = 3.0 at the hole. We model a
quarter plate (symmetry) with d/W = 0.05, where the finite-width (Howland)
correction is ~1 % - inside the 5 % gate. Plane stress CPS6 elements,
consistent edge loads on the far edge.

Acceptance: |Kt_FEA - 3.0| / 3.0 < 5 %.
"""

import json
import pathlib

import numpy as np
import pytest

from node_kit.mesh import plate_with_hole_2d, nodes_where
from node_kit.fea import (
    Deck, ElementBlock, MaterialDef, Section, Step,
    write_inp, run_ccx, parse_frd,
)

E = 200_000.0       # MPa
NU = 0.3
R = 5.0             # hole radius, mm
HALF_W = 100.0      # half plate width -> d/W = 0.05
HALF_L = 200.0
SIGMA = 100.0       # applied far-field tension, MPa
THICK = 1.0
HOLE_SIZE = 0.4     # element size at the hole
FAR_SIZE = 8.0
KT_TARGET = 3.0
TOL_PCT = 5.0

ARTIFACTS = pathlib.Path(__file__).resolve().parents[1] / "runs" / "validation" / "gate2"

TRI6_EDGES = [(0, 1, 3), (1, 2, 4), (2, 0, 5)]


def _consistent_edge_loads(mesh, cells, on_edge, sigma, thickness):
    """Consistent nodal forces for a uniform traction on quadratic tri edges:
    corner nodes take 1/6 of the edge force each, midside takes 4/6."""
    pts = mesh.points
    loads: dict[int, float] = {}
    for conn in cells:
        for (a, b, m) in TRI6_EDGES:
            na, nb, nm = int(conn[a]), int(conn[b]), int(conn[m])
            if on_edge(*pts[na]) and on_edge(*pts[nb]) and on_edge(*pts[nm]):
                length = float(np.linalg.norm(pts[nb][:2] - pts[na][:2]))
                f_edge = sigma * thickness * length
                loads[na] = loads.get(na, 0.0) + f_edge / 6.0
                loads[nb] = loads.get(nb, 0.0) + f_edge / 6.0
                loads[nm] = loads.get(nm, 0.0) + 4.0 * f_edge / 6.0
    return loads


def test_gate2_hole_stress_concentration():
    mesh = plate_with_hole_2d(HALF_L, HALF_W, R, FAR_SIZE, HOLE_SIZE)
    tri6 = [b.data for b in mesh.cells if b.type == "triangle6"]
    assert tri6, "no triangle6 cells produced"
    cells = np.vstack(tri6)

    xsym = nodes_where(mesh, lambda x, y, z: abs(x) < 1e-6)
    ysym = nodes_where(mesh, lambda x, y, z: abs(y) < 1e-6)
    loads = _consistent_edge_loads(
        mesh, cells, lambda x, y, z: abs(x - HALF_L) < 1e-6, SIGMA, THICK)
    assert loads, "no load edge found"
    total = sum(loads.values())
    assert abs(total - SIGMA * THICK * HALF_W) < 1e-6 * SIGMA * THICK * HALF_W

    deck = Deck(
        points=mesh.points,
        blocks=[ElementBlock("PLATE", "CPS6", cells)],
        nsets={"XSYM": xsym, "YSYM": ysym},
        materials=[MaterialDef("STEEL", E, NU)],
        sections=[Section("PLATE", "STEEL", thickness=THICK)],
        base_boundaries=[("XSYM", 1, 1, 0.0), ("YSYM", 2, 2, 0.0)],
        steps=[Step(
            name="tension",
            cloads=[(int(n) + 1, 1, f) for n, f in sorted(loads.items())],
        )],
    )

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    write_inp(deck, str(ARTIFACTS / "gate2.inp"))
    res = run_ccx(str(ARTIFACTS), "gate2")
    frd = parse_frd(res["frd"])

    stress = frd["STRESS"][-1]
    pts = mesh.points
    rim = nodes_where(
        mesh, lambda x, y, z: abs(np.hypot(x, y) - R) < 0.05)
    sxx_rim = np.array([stress[int(n) + 1][0] for n in rim
                        if int(n) + 1 in stress])
    kt_fea = float(sxx_rim.max()) / SIGMA
    err_pct = abs(kt_fea - KT_TARGET) / KT_TARGET * 100.0

    (ARTIFACTS / "result.json").write_text(json.dumps({
        "kt_target": KT_TARGET,
        "kt_fea": kt_fea,
        "error_pct": err_pct,
        "d_over_W": 2 * R / (2 * HALF_W),
        "n_elements": int(len(cells)),
    }, indent=2))

    assert err_pct < TOL_PCT, (
        f"gate 2 FAILED: Kt_FEA = {kt_fea:.3f}, error {err_pct:.2f}%"
    )
