"""Milestone 7 machinery tests (fast - the NSGA integration itself is
exercised by the real search run, whose results land in runs/)."""

import numpy as np
import pandas as pd
import pytest

from node_kit import search as S
from node_kit import report as R


def test_decode_known_point_is_valid():
    # wall, engagement, rib_t, rib_depth, web, boss, plate, rows, dia
    x = [13.5, 165.0, 10.0, 12.0, 10.0, 60.0, 12.0, 0.2, 0.8]
    p = S.decode(x)
    assert p.wall_thickness == pytest.approx(13.5)
    assert p.spine_split is True          # architecture E baked in
    assert p.spine_bolt_diameter == S.SPINE_BOLT_D
    assert p.base_plate_thickness == pytest.approx(12.0)
    assert p.bolt_rows == 2 and p.bolt_diameter == 16.0
    assert p.bolt_pitch == pytest.approx(52.0)      # 3.25d for M16
    assert p.bolt_edge_distance == pytest.approx(32.0)
    # brackets sit outboard of bolt columns, between plate and bolt rows
    from node_kit.geometry import validate_hole_layout
    validate_hole_layout(p)   # must not raise
    ss = sorted({abs(s) for (s, _) in p.bracket_bolt_pattern})
    assert ss == [pytest.approx(p.bolt_gauge / 2 + 10.0)]


def test_search_lcs_include_stacked_bearing():
    ids = [lc.case_id for lc in S.search_load_cases()]
    assert ids == ["LC1S", "LC2", "LCC"]
    lc1s = S.search_load_cases()[0]
    assert lc1s.axial_N > 0


def test_candidate_id_deterministic():
    x = [10.0, 180.0, 9.0, 12.0, 10.0, 48.0]
    assert S.candidate_id_for(x) == S.candidate_id_for(list(x))
    assert S.candidate_id_for(x) != S.candidate_id_for([10.1] + x[1:])


def test_cast_violation_directions():
    row = pd.Series({
        "cast_min_wall_value": 6.0, "cast_min_wall_limit": 8.0,     # fail ge
        "cast_hot_spot_value": 20.0, "cast_hot_spot_limit": 24.0,   # pass le
        "cast_section_ratio_value": 1.5, "cast_section_ratio_limit": 2.0,
        "cast_draft_value": 2.0, "cast_draft_limit": 1.5,
        "cast_fillet_value": 8.0, "cast_fillet_limit": 6.0,
        "cast_undercut_value": 0.0, "cast_undercut_limit": 0.005,
    })
    v = S.cast_violation(row)
    assert v == pytest.approx((8.0 - 6.0) / 8.0)   # min_wall violation governs
    row["cast_min_wall_value"] = 9.0
    assert S.cast_violation(row) < 0               # all pass -> negative


def test_pareto_mask():
    F = np.array([[1.0, 5.0], [2.0, 3.0], [3.0, 1.0], [3.0, 4.0], [2.5, 3.5]])
    mask = R.pareto_mask(F)
    assert list(mask) == [True, True, True, False, False]


def test_aggregate_and_pick(tmp_path):
    rows = []
    for cid, mass, ratio, cast_ok in [("a", 9.0, 0.7, True),
                                      ("b", 6.0, 0.85, True),
                                      ("c", 5.0, 1.2, True),
                                      ("d", 4.0, 0.5, False)]:
        rows.append({
            "candidate_id": cid, "mass_kg": mass,
            "ratio_bearing_J332": ratio, "ratio_casting_vm": ratio / 2,
            "castability_all_pass": cast_ok, "spacing_limits_pass": True,
            "joint_stiffness_N_mm": 1e6, "load_case": "LCC",
            "wall_thickness": 10.0, "engagement_length": 180.0,
            "rib_thickness": 9.0, "rib_depth": 12.0,
            "corner_web_thickness": 10.0, "boss_diameter": 48.0,
            "base_plate_thickness": 12.0, "bolt_rows": 3,
            "bolt_diameter": 12.0, "governing_limit_state": "x",
        })
    agg = R.aggregate(pd.DataFrame(rows))
    assert len(agg) == 4
    assert agg.set_index("candidate_id").loc["c"].feasible == False  # noqa
    assert agg.set_index("candidate_id").loc["d"].feasible == False  # noqa
    rec = R.pick_recommended(agg)
    assert rec.candidate_id == "b"   # lightest feasible with margin <= 0.9
