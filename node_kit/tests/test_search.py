"""Milestone 7 machinery tests (fast - the NSGA integration itself is
exercised by the real search run, whose results land in runs/)."""

import numpy as np
import pandas as pd
import pytest

from node_kit import search as S
from node_kit import report as R


def test_decode_midpoint_is_valid():
    x = (S.XL + S.XU) / 2
    p = S.decode(x)
    assert p.wall_thickness == pytest.approx(12.0)
    # bracket pattern scaled with engagement
    zs = sorted({z for (_, z) in p.bracket_bolt_pattern})
    L = p.engagement_length
    assert zs[0] == pytest.approx(0.3 * L, abs=0.1)
    assert zs[1] == pytest.approx(0.7 * L, abs=0.1)


def test_candidate_id_deterministic():
    x = [10.0, 180.0, 9.0, 12.0, 10.0, 48.0]
    assert S.candidate_id_for(x) == S.candidate_id_for(list(x))
    assert S.candidate_id_for(x) != S.candidate_id_for([10.1] + x[1:])


def test_search_load_cases_clamp_architecture():
    lcs = S.search_load_cases()
    ids = [lc.case_id for lc in lcs]
    assert ids == ["LC2", "LCC"]
    assert all(lc.axial_N == 0.0 for lc in lcs), \
        "clamp architecture: no stacking axial in the casting"


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
            "governing_limit_state": "x",
        })
    agg = R.aggregate(pd.DataFrame(rows))
    assert len(agg) == 4
    assert agg.set_index("candidate_id").loc["c"].feasible == False  # noqa
    assert agg.set_index("candidate_id").loc["d"].feasible == False  # noqa
    rec = R.pick_recommended(agg)
    assert rec.candidate_id == "b"   # lightest feasible with margin <= 0.9
