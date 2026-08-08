"""VALIDATION GATE 1: cantilever tip deflection vs delta = P*L^3 / (3*E*I).

Acceptance: |FEA - theory| / theory < 2 %.

Benchmark constants below are inputs to the closed-form case, not design
values. Geometry is slender (L/h = 20) so Euler-Bernoulli theory applies;
at this ratio the shear contribution the 3D model adds is ~0.2 % and the
clamped-face Poisson restraint subtracts a similar amount, both inside the
2 % gate. Load is applied as an equal nodal split over the tip face (total
force exact; local Saint-Venant artifacts do not affect mean tip
deflection). Documented in README's simplifications log.
"""

import json
import pathlib

import numpy as np
import pytest

from node_kit.mesh import cantilever_beam_mesh, volume_cells, nodes_where
from node_kit.fea import (
    Deck, ElementBlock, MaterialDef, Section, Step,
    write_inp, run_ccx, parse_frd,
)

E = 200_000.0     # MPa
NU = 0.3
L = 200.0         # mm
B = 10.0          # mm (width, y)
H = 10.0          # mm (depth, z)
P = 100.0         # N, applied in -z at the tip
ELEM = 2.5        # mm
TOL_PCT = 2.0

ARTIFACTS = pathlib.Path(__file__).resolve().parents[1] / "runs" / "validation" / "gate1"


def test_gate1_cantilever_tip_deflection():
    mesh = cantilever_beam_mesh(length=L, depth=H, width=B, elem_size=ELEM)
    cells = volume_cells(mesh, "tetra10")

    fix = nodes_where(mesh, lambda x, y, z: abs(x) < 1e-6)
    tip = nodes_where(mesh, lambda x, y, z: abs(x - L) < 1e-6)
    assert len(fix) > 10 and len(tip) > 10

    deck = Deck(
        points=mesh.points,
        blocks=[ElementBlock("BEAM", "C3D10", cells)],
        nsets={"FIX": fix, "TIP": tip},
        materials=[MaterialDef("STEEL", E, NU)],
        sections=[Section("BEAM", "STEEL")],
        base_boundaries=[("FIX", 1, 3, 0.0)],
        steps=[Step(
            name="bend",
            cloads=[(int(n) + 1, 3, -P / len(tip)) for n in tip],
        )],
    )

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    write_inp(deck, str(ARTIFACTS / "gate1.inp"))
    res = run_ccx(str(ARTIFACTS), "gate1")
    frd = parse_frd(res["frd"])

    disp = frd["DISP"][-1]
    tip_ids = [int(n) + 1 for n in tip]
    uz = np.array([disp[i][2] for i in tip_ids])
    delta_fea = -float(uz.mean())

    I = B * H ** 3 / 12.0
    delta_theory = P * L ** 3 / (3.0 * E * I)
    err_pct = abs(delta_fea - delta_theory) / delta_theory * 100.0

    (ARTIFACTS / "result.json").write_text(json.dumps({
        "delta_theory_mm": delta_theory,
        "delta_fea_mm": delta_fea,
        "error_pct": err_pct,
        "n_elements": int(len(cells)),
    }, indent=2))

    assert err_pct < TOL_PCT, (
        f"gate 1 FAILED: {err_pct:.2f}% error "
        f"(FEA {delta_fea:.4f} vs theory {delta_theory:.4f} mm)"
    )
