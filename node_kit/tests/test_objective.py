"""Milestone 6 test: one candidate end-to-end on a coarse mesh, one LC.

This is an integration test of the whole chain (geometry -> castability ->
assembly FEA -> AISI -> contract row). Coarse meshes keep it to a couple of
minutes; production numbers come from the default AssemblySizes.
"""

import numpy as np
import pytest

from node_kit.params import NodeParams, default_load_cases
from node_kit.objective import evaluate_candidate, COARSE

CONTRACT_COLUMNS = [
    # params (spot checks - as_flat_dict covers all fields)
    "post_size", "post_thickness", "wall_thickness", "bolt_diameter",
    "bracket_bolt_pattern", "casting_material_name",
    # load case
    "load_case", "lc_shear_x_N",
    # results
    "mass_kg", "casting_peak_vm_MPa", "post_wall_peak_vm_MPa",
    "joint_stiffness_N_mm",
    "ratio_bearing_J332", "ratio_bolt_shear",
    "ratio_bolt_tension_interaction", "ratio_net_section",
    "ratio_block_shear", "ratio_casting_vm", "ratio_post_wall_vm",
    "spacing_limits_pass",
    "cast_min_wall_pass", "cast_undercut_pass", "cast_hot_spot_pass",
    "castability_all_pass",
    "lc_pass", "candidate_pass", "governing_limit_state",
    "solve_wallclock_s", "git_sha", "timestamp_utc", "rng_seed",
    "design_method", "factors_unverified",
]


@pytest.fixture(scope="module")
def result(tmp_path_factory):
    wd = tmp_path_factory.mktemp("m6")
    lcs = [lc for lc in default_load_cases() if lc.case_id == "LC3"]
    return evaluate_candidate(
        NodeParams(), lcs, workdir=str(wd), sizes=COARSE,
        parquet_out=str(wd / "results.parquet"))


def test_contract_columns_present(result):
    missing = [c for c in CONTRACT_COLUMNS if c not in result.columns]
    assert not missing, f"contract columns missing: {missing}"


def test_physical_sanity(result):
    row = result.iloc[0]
    assert 2.0 < row.mass_kg < 60.0
    # linear elastic screening values must be positive and finite
    assert 0 < row.casting_peak_vm_MPa < 5000
    assert 0 < row.post_wall_peak_vm_MPa < 5000
    assert row.joint_stiffness_N_mm > 1e3
    assert 0 < row.ratio_bearing_J332 < 100
    assert row.solve_wallclock_s > 1.0
    assert row.factors_unverified == True  # noqa: E712


def test_bolt_forces_balance_applied_shear(result):
    """The bolt group + contact must carry the applied bracket shear; the
    spring-recovered bolt forces on the loaded leg should sum to the same
    order as the applied load (contact friction is off, so the vertical
    path is bolts only up to contact normal effects)."""
    import json

    row = result.iloc[0]
    forces = json.loads(row.bolt_forces_json)
    v_sum = sum(b["shear_N"] for b in forces if b["leg"] == "A")
    applied = abs(row.lc_shear_x_N)
    assert v_sum == pytest.approx(applied, rel=0.35), (
        f"leg-A bolt shear sum {v_sum:.0f} N vs applied {applied:.0f} N"
    )


def test_parquet_written(result, tmp_path_factory):
    import pandas as pd
    import glob

    files = glob.glob(str(tmp_path_factory.getbasetemp() / "m6*" / "results.parquet"))
    assert files
    df = pd.read_parquet(files[0])
    assert len(df) == 1
