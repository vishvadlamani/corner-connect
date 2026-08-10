"""Load-layer tests: primitive-action architecture (reviewer finding 6).

The contract magnitudes are frozen against the pre-refactor values - the
refactor changed the DERIVATION (primitives + one combination layer), not
the numbers.
"""

import pytest

from node_kit.params import (
    LoadCase,
    NodeActions,
    combine,
    default_load_cases,
    primitive_actions,
    superposition_diagnostics,
)


def _by_id(lcs: list[LoadCase]) -> dict[str, LoadCase]:
    return {lc.case_id: lc for lc in lcs}


def test_contract_magnitudes_frozen():
    lcs = _by_id(default_load_cases(5))
    assert set(lcs) == {"LC1", "LC2", "LC3", "LC4", "LC5"}  # LC6 retired
    assert lcs["LC1"].axial_N == pytest.approx(203040.0)
    assert lcs["LC2"].rod_tension_N == pytest.approx(86400.0)
    assert lcs["LC3"].shear_x_N == pytest.approx(40608.0)
    assert lcs["LC4"].shear_y_N == pytest.approx(40608.0)   # LC3 mirrored
    assert lcs["LC5"].axial_N == pytest.approx(203040.0)
    assert lcs["LC5"].shear_x_N == pytest.approx(40608.0)
    assert lcs["LC5"].shear_y_N == pytest.approx(8100.0)    # facade, dead only
    # envelope cases carry ONLY their named components
    assert lcs["LC2"].axial_N == 0.0 and lcs["LC2"].shear_x_N == 0.0
    assert lcs["LC1"].rod_tension_N == 0.0


def test_primitives_store_gross_actions():
    prim = primitive_actions(5)
    # W is GROSS overturning tension - dead relief lives in D, not in W
    assert prim["W"].rod_tension_N == pytest.approx(135000.0)
    assert prim["D"].rod_tension_N == pytest.approx(-54000.0)
    # LC2 = 0.9*D + 1.0*W on the rod component, combined exactly once
    assert (0.9 * prim["D"].rod_tension_N + 1.0 * prim["W"].rod_tension_N
            == pytest.approx(86400.0))
    assert prim["L"].shear_y_N == 0.0     # facade carries no live load


def test_combine_masks_and_clamps():
    prim = {"D": NodeActions(rod_tension_N=-100.0, axial_N=100.0),
            "W": NodeActions(rod_tension_N=50.0)}
    # gravity fully suppresses uplift -> clamps to zero, not negative
    lc = combine("T1", "clamp", {"D": 0.9, "W": 1.0},
                 components=("rod_tension",), primitives=prim)
    assert lc.rod_tension_N == 0.0
    # masked components are zeroed even when the sum is nonzero
    assert lc.axial_N == 0.0
    with pytest.raises(ValueError):
        combine("T2", "bad", {"D": 1.0}, components=("bending",),
                primitives=prim)


def test_diagnostics_are_labelled_not_contract():
    diag = _by_id(superposition_diagnostics(5))
    assert set(diag) == {"LCS1", "LCS2"}
    # LCS1 reproduces the retired LC6 (kept as a lower-bound diagnostic)
    assert diag["LCS1"].rod_tension_N == pytest.approx(86400.0)
    assert diag["LCS1"].shear_x_N == pytest.approx(9720.0)
    # LCS2 is the maximum-cancellation sensitivity case, ex-'bounding'
    assert diag["LCS2"].shear_x_N == pytest.approx(40608.0)
    for lc in diag.values():
        assert "NOT an envelope" in lc.description or \
               "sensitivity" in lc.description


def test_n_storeys_validation():
    with pytest.raises(ValueError):
        primitive_actions(0)
