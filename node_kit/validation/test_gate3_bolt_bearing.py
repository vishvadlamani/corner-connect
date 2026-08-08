"""VALIDATION GATE 3: single-bolt lap-shear coupon vs AISI hand calculation.

Model: half-symmetric thin CFS sheet (97 mil design thickness) with a
standard clearance hole, rigid pin displacement-driven along -x, finite
sliding surface-to-surface contact, elastic-plastic sheet (Fy -> Fu
hardening), NLGEOM. The far end of the sheet is gripped. Geometry is sized
so BOLT BEARING governs (net section and shear-out margins are wide).

What is being validated: that our FEA modelling recipe for bolt bearing in
thin sheet (the same recipe mesh.py/fea.py will use in the node assembly
model) reproduces bearing behaviour consistent with the AISI S100 hand
calculation in checks/aisi_s100.py.

Acceptance criteria (engineering-judgment band, FLAGGED FOR ENGINEER
REVIEW - unlike gates 1 and 2 there is no exact closed form):
  1. FEA peak force must EXCEED the nominal J3.3.2 capacity (the code value
     is a lower-bound calibration at a hole-deformation serviceability
     limit; a materially accurate ultimate simulation must sit above it).
  2. FEA peak force must stay BELOW 1.15 x the J3.3.1 capacity with
     m_f = 1.33 (the standard's most generous bearing configuration;
     exceeding it substantially would mean our contact/plasticity model is
     unconservatively stiff-strong).
Both ratios are reported either way.

The pin is held vertical (all rigid-body rotations fixed): tilting is
deliberately suppressed, so the comparison bounds use the washered /
double-shear-inside-sheet m_f values, not the no-washer single-shear value.
Documented in README's simplifications log.
"""

import json
import pathlib

import numpy as np
import pytest

from node_kit.params import CFS_GRADE_50, GAUGE_TABLE_MM
from node_kit.checks.aisi_s100 import (
    bolt_bearing_no_deformation, bolt_bearing_with_deformation,
)
from node_kit.mesh import (
    lap_coupon_mesh, volume_cells, nodes_where, faces_where,
)
from node_kit.fea import (
    Deck, ElementBlock, MaterialDef, Section, Step,
    write_inp, run_ccx, parse_dat, dat_series,
)

# --- coupon definition (mm, N, MPa) ---------------------------------------
T = GAUGE_TABLE_MM[97]["design"]     # 2.583 mm sheet
BOLT_D = 12.0
HOLE_D = 12.8                        # standard clearance hole for M12
SHEET_W = 84.0                       # 7*d: net section does not govern
SHEET_LEN = 160.0
END_DIST = 42.0                      # 3.5*d to the free end: no shear-out
PIN_LEN = T + 16.0
FINE = 1.3
COARSE = 9.0
PIN_TRAVEL = 2.5                     # mm, displacement-driven
FU = CFS_GRADE_50.Fu
FY = CFS_GRADE_50.Fy
E_SHEET = CFS_GRADE_50.E
PLASTIC = [(FY, 0.0), (FU, 0.10)]    # bilinear hardening to Fu at 10% strain
CONTACT_K = 1.0e6                    # MPa/mm, linear pressure-overclosure

ARTIFACTS = pathlib.Path(__file__).resolve().parents[1] / "runs" / "validation" / "gate3"


def test_gate3_bolt_bearing_vs_aisi():
    mesh = lap_coupon_mesh(
        sheet_w=SHEET_W, sheet_len=SHEET_LEN, sheet_t=T,
        hole_d=HOLE_D, hole_cx=END_DIST,
        pin_d=BOLT_D, pin_len=PIN_LEN,
        fine=FINE, coarse=COARSE,
    )
    cells = volume_cells(mesh, "tetra10")
    pts = mesh.points

    # split sheet / pin cells geometrically (two disconnected volumes)
    cx = END_DIST
    centroids = pts[cells[:, :4]].mean(axis=1)
    r_c = np.hypot(centroids[:, 0] - cx, centroids[:, 1])
    split_r = (BOLT_D / 2 + HOLE_D / 2) / 2
    pin_mask = r_c < split_r
    sheet_cells = cells[~pin_mask]
    pin_cells = cells[pin_mask]
    assert len(pin_cells) > 100 and len(sheet_cells) > 1000

    pin_node_idx = np.unique(pin_cells)
    pin_nodes = set(int(i) for i in pin_node_idx)

    grip = nodes_where(mesh, lambda x, y, z: abs(x - SHEET_LEN) < 1e-6)
    sym_all = nodes_where(mesh, lambda x, y, z: abs(y) < 1e-6)
    sym_sheet = np.array([i for i in sym_all if int(i) not in pin_nodes],
                         dtype=int)
    assert len(grip) > 10 and len(sym_sheet) > 10

    # contact surfaces (element ids are 1-based and sheet block comes first)
    hole_r, pin_r = HOLE_D / 2, BOLT_D / 2
    sheet_faces = faces_where(
        mesh, sheet_cells,
        lambda x, y, z: abs(np.hypot(x - cx, y) - hole_r) < 0.05)
    pin_faces = faces_where(
        mesh, pin_cells,
        lambda x, y, z: abs(np.hypot(x - cx, y) - pin_r) < 0.05)
    assert sheet_faces and pin_faces
    n_sheet = len(sheet_cells)
    surf_hole = [(ei + 1, fid) for (ei, fid) in sheet_faces]
    surf_pin = [(ei + 1 + n_sheet, fid) for (ei, fid) in pin_faces]

    n_mesh = len(pts)
    ref_id, rot_id = n_mesh + 1, n_mesh + 2

    deck = Deck(
        points=pts,
        blocks=[
            ElementBlock("SHEET", "C3D10", sheet_cells),
            ElementBlock("PIN", "C3D10", pin_cells),
        ],
        nsets={
            "GRIP": grip,
            "SHEETSYM": sym_sheet,
            "PINALL": pin_node_idx,
        },
        raw_nsets={"REFSET": [ref_id]},
        extra_nodes={
            ref_id: (cx, 0.0, T / 2),
            rot_id: (cx, 0.0, T / 2),
        },
        materials=[
            MaterialDef("CFS", E_SHEET, CFS_GRADE_50.nu, plastic=PLASTIC),
            MaterialDef("PINSTEEL", 200_000.0, 0.3),
        ],
        sections=[Section("SHEET", "CFS"), Section("PIN", "PINSTEEL")],
        surfaces={"SHOLE": surf_hole, "SPIN": surf_pin},
        rigid_bodies=[("PINALL", ref_id, rot_id)],
        interactions=[("SI1", CONTACT_K)],
        contact_pairs=[("SI1", "SHOLE", "SPIN")],
        base_boundaries=[
            ("GRIP", 1, 3, 0.0),
            ("SHEETSYM", 2, 2, 0.0),
        ],
        steps=[Step(
            name="bear",
            nlgeom=True,
            static_line="0.02, 1.0, 1e-7, 0.05",
            max_increments=500,
            boundaries=[
                (ref_id, 1, 1, -PIN_TRAVEL),
                (ref_id, 2, 3, 0.0),
                (rot_id, 1, 3, 0.0),
            ],
            node_prints=[("REFSET", "U", False), ("REFSET", "RF", False)],
        )],
    )

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    write_inp(deck, str(ARTIFACTS / "gate3.inp"))
    res = run_ccx(str(ARTIFACTS), "gate3", timeout_s=5400)

    entries = parse_dat(res["dat"])
    rf = dat_series(entries, "forces", "REFSET")
    assert rf, "no reaction-force history in .dat"
    # rows: node id, fx, fy, fz ; half model -> double the force
    history = [
        {"time": t, "pin_travel_mm": t * PIN_TRAVEL,
         "force_N": 2.0 * abs(row[1])}
        for (t, row) in rf
    ]
    f_max = max(h["force_N"] for h in history)

    pn_332, _, ref_332 = bolt_bearing_with_deformation(BOLT_D, T, FU)
    pn_331_sw, _, ref_331 = bolt_bearing_no_deformation(
        BOLT_D, T, FU, "single_shear_with_washers")
    pn_331_ds, _, _ = bolt_bearing_no_deformation(
        BOLT_D, T, FU, "double_shear_inside_sheet_with_washers")

    ratio_low = f_max / pn_332
    ratio_high = f_max / pn_331_ds

    (ARTIFACTS / "result.json").write_text(json.dumps({
        "aisi_J332_with_deformation_N": pn_332,
        "aisi_J331_single_shear_washers_N": pn_331_sw,
        "aisi_J331_double_shear_inside_N": pn_331_ds,
        "fea_peak_force_N": f_max,
        "fea_over_J332": ratio_low,
        "fea_over_J331_ds": ratio_high,
        "history": history,
        "clause_refs": [ref_332, ref_331],
        "n_elements": int(len(cells)),
    }, indent=2))

    assert ratio_low >= 1.0, (
        f"gate 3 FAILED (unconservative code side): FEA peak {f_max/1e3:.1f} kN "
        f"< J3.3.2 nominal {pn_332/1e3:.1f} kN"
    )
    assert f_max <= 1.15 * pn_331_ds, (
        f"gate 3 FAILED (FEA suspiciously strong): FEA peak {f_max/1e3:.1f} kN "
        f"> 1.15 x J3.3.1(mf=1.33) {1.15*pn_331_ds/1e3:.1f} kN"
    )
