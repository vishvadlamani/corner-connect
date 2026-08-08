"""Milestone 4 tests for checks/aisi_s100.py.

Two tiers:
  1. LIVE tests - formula self-consistency, breakpoint continuity,
     monotonicity, regression locks. These verify the code implements the
     equations as written (they cannot verify the equations against the
     standard - that is tier 2 plus the engineer's review).
  2. WORKED-EXAMPLE tests - skipped placeholders awaiting numbers from the
     AISI Cold-Formed Steel Design Manual. Each names exactly which value
     to supply. DO NOT unskip without inserting the printed value.
"""

import math

import pytest

from node_kit.checks import aisi_s100 as A


# ---------------------------------------------------------------------------
# bearing factor C - Table J3.3.1-1 breakpoints and continuity
# ---------------------------------------------------------------------------
def test_C_regions():
    assert A.bearing_factor_C(d=10, t=2.0) == 3.0            # d/t = 5
    assert A.bearing_factor_C(d=16, t=1.0) == pytest.approx(2.4)   # d/t = 16
    assert A.bearing_factor_C(d=30, t=1.0) == 1.8            # d/t = 30


def test_C_continuous_at_breakpoints():
    eps = 1e-9
    assert A.bearing_factor_C(10 - eps, 1.0) == pytest.approx(
        A.bearing_factor_C(10 + eps, 1.0), abs=1e-6)
    assert A.bearing_factor_C(22 - eps, 1.0) == pytest.approx(
        A.bearing_factor_C(22 + eps, 1.0), abs=1e-6)


def test_bearing_monotonic_in_thickness():
    caps = [A.bolt_bearing_with_deformation(12.0, t, 450.0)[0]
            for t in (1.0, 1.5, 2.0, 2.583)]
    assert caps == sorted(caps)


def test_bearing_regression_gate3_value():
    # locks the value gate 3 validated against (97 mil, M12, Fu=450)
    Pn, _, ref = A.bolt_bearing_with_deformation(12.0, 2.583, 450.0)
    assert Pn == pytest.approx(27927.3, rel=1e-4)
    assert "J3.3.2" in ref


def test_bearing_wrapper_applicability():
    with pytest.raises(ValueError, match="applicability"):
        A.bolt_bearing(12.0, 0.4, 450.0, consider_hole_deformation=True)
    # no-deformation path has no thickness screen
    Pn, _, _ = A.bolt_bearing(12.0, 0.4, 450.0, consider_hole_deformation=False)
    assert Pn > 0


# ---------------------------------------------------------------------------
# bolt shear / tension
# ---------------------------------------------------------------------------
def test_bolt_shear_formula():
    d, Fu = 12.0, 800.0
    Pn, ls, ref = A.bolt_shear(d, Fu, threads_in_shear_plane=True)
    assert Pn == pytest.approx(math.pi * 36 * 0.450 * 800)
    assert "UNVERIFIED" in ref            # placeholder Fnv basis is flagged
    Pn2, _, ref2 = A.bolt_shear(d, Fu, Fnv=372.0)
    assert Pn2 == pytest.approx(math.pi * 36 * 372.0)
    assert "supplied by caller" in ref2


def test_bolt_shear_planes():
    one = A.bolt_shear(12.0, 800.0)[0]
    two = A.bolt_shear(12.0, 800.0, shear_planes=2)[0]
    assert two == pytest.approx(2 * one)


def test_interaction_limits():
    d, Fu = 12.0, 800.0
    full, _, _ = A.bolt_tension(d, Fu)
    # no shear -> full tension capacity (cap at Fnt engages)
    no_shear, _, _ = A.shear_tension_interaction(0.0, 0.0, d, Fu)
    assert no_shear == pytest.approx(full)
    # increasing shear monotonically reduces tension capacity
    caps = [A.shear_tension_interaction(v, 0.0, d, Fu)[0]
            for v in (20e3, 30e3, 40e3)]
    assert caps == sorted(caps, reverse=True)
    # absurd shear floors at zero, never negative
    floored, _, _ = A.shear_tension_interaction(500e3, 0.0, d, Fu)
    assert floored == 0.0


# ---------------------------------------------------------------------------
# rupture of the connected part
# ---------------------------------------------------------------------------
def test_net_section_usl_cap():
    An, Fu = 100.0, 450.0
    # narrow tributary width (s = 3d) -> 0.1 + 3d/s = 1.1, caps at 1.0
    Pn, _, _ = A.net_section_rupture(An, Fu, d=12.0, s=36.0)
    assert Pn == pytest.approx(An * Fu)
    # wide tributary width -> stronger shear lag, lower efficiency
    Pn2, _, ref = A.net_section_rupture(An, Fu, d=12.0, s=60.0)
    assert Pn2 == pytest.approx((0.1 + 3 * 12 / 60) * An * Fu)
    assert Pn2 < Pn
    assert "UNVERIFIED" in ref


def test_block_shear_paths():
    Fy, Fu = 345.0, 450.0
    # geometry where the shear-yield path governs
    Pn, gov, _ = A.block_shear_rupture(
        Agv=200.0, Anv=180.0, Ant=50.0, Fy=Fy, Fu=Fu)
    expected = min(0.6 * Fy * 200 + Fu * 50, 0.6 * Fu * 180 + Fu * 50)
    assert Pn == pytest.approx(expected)
    assert "shear-yield" in gov
    # exaggerated gross area flips governance to the rupture path
    _, gov2, _ = A.block_shear_rupture(
        Agv=2000.0, Anv=100.0, Ant=50.0, Fy=Fy, Fu=Fu)
    assert "shear-rupture" in gov2


def test_shear_rupture_formula():
    Vn, _, _ = A.shear_rupture(Anv=100.0, Fu=450.0)
    assert Vn == pytest.approx(0.6 * 450 * 100)


# ---------------------------------------------------------------------------
# spacing / edge limits
# ---------------------------------------------------------------------------
def test_spacing_limits():
    ok, checks, _ = A.spacing_and_edge_limits(
        d=12.0, spacing=40.0, edge_distance=25.0, end_distance=25.0)
    assert ok
    bad, checks, _ = A.spacing_and_edge_limits(
        d=12.0, spacing=30.0, edge_distance=25.0, end_distance=25.0)
    assert not bad and not checks["spacing"][2]


# ---------------------------------------------------------------------------
# DCR gatekeeping
# ---------------------------------------------------------------------------
def test_dcr_refuses_unverified_factors():
    with pytest.raises(A.UnverifiedFactorError):
        A.demand_capacity_ratio(10e3, 30e3, "bolt_shear", "LRFD")


def test_dcr_math_with_override():
    dcr_lrfd = A.demand_capacity_ratio(
        10e3, 30e3, "bolt_shear", "LRFD", allow_unverified=True)
    e = A.SAFETY_FACTORS["bolt_shear"]
    assert dcr_lrfd == pytest.approx(10e3 / (e.phi * 30e3))
    dcr_asd = A.demand_capacity_ratio(
        10e3, 30e3, "bolt_shear", "ASD", allow_unverified=True)
    assert dcr_asd == pytest.approx(10e3 * e.omega / 30e3)


def test_single_bolt_summary_governing():
    # thin sheet: bearing must govern over bolt shear
    out = A.single_bolt_summary(12.0, 1.146, 450.0, 800.0)
    assert out["governing"] == "bearing"


# ---------------------------------------------------------------------------
# TIER 2: worked examples from the AISI Cold-Formed Steel Design Manual.
# Each skip names the exact number the owner/engineer must supply from the
# printed manual. Insert the value, delete the skip, run.
# ---------------------------------------------------------------------------
@pytest.mark.skip(reason="TODO: supply AISI Design Manual worked-example "
                         "value: single-bolt BEARING capacity example "
                         "(sheet t, d, Fu and the manual's Pn)")
def test_worked_example_bearing():
    raise NotImplementedError


@pytest.mark.skip(reason="TODO: supply AISI Design Manual worked-example "
                         "value: NET SECTION rupture of bolted flat sheet "
                         "(geometry and the manual's Pn)")
def test_worked_example_net_section():
    raise NotImplementedError


@pytest.mark.skip(reason="TODO: supply AISI Design Manual worked-example "
                         "value: BLOCK SHEAR example (areas and Pn)")
def test_worked_example_block_shear():
    raise NotImplementedError


@pytest.mark.skip(reason="TODO: supply printed AISI Table bolt Fnv/Fnt for "
                         "the actual fastener spec (grade, threads in/out)")
def test_worked_example_bolt_shear():
    raise NotImplementedError
