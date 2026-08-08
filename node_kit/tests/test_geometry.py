"""Milestone 2 geometry tests: watertight solid, mass sanity, layout rules."""

import math

import pytest

from node_kit.params import NodeParams, gauge_to_design_thickness
from node_kit.geometry import (
    build_node,
    export_step,
    export_stl,
    post_bolt_positions,
    bracket_hole_positions,
    rib_levels,
    validate_hole_layout,
)


@pytest.fixture(scope="module")
def default_build():
    return build_node(NodeParams())


# ---------------------------------------------------------------------------
# params
# ---------------------------------------------------------------------------
def test_gauge_mapping():
    assert gauge_to_design_thickness(97) == pytest.approx(2.583, abs=1e-3)
    assert gauge_to_design_thickness(33) == pytest.approx(0.879, abs=1e-3)
    with pytest.raises(ValueError):
        gauge_to_design_thickness(50)


def test_post_thickness_derived_from_gauge():
    p = NodeParams(post_gauge_mil=54)
    assert p.post_thickness == pytest.approx(1.438, abs=1e-3)


def test_invalid_params_rejected():
    with pytest.raises(ValueError):
        NodeParams(wall_thickness=-1)
    with pytest.raises(ValueError):
        NodeParams(boss_diameter=30.0, rod_hole_diameter=22.0)  # no annulus
    with pytest.raises(ValueError):
        NodeParams(bolt_rows=10, bolt_pitch=40)  # group taller than engagement


def test_rod_center_offset_keeps_boss_out_of_post():
    p = NodeParams()
    # boss surface must not cross the post corner point
    cx, cy = p.rod_center_xy
    corner = p.post_size / 2
    dist = math.hypot(cx - corner, cy - corner)
    assert dist >= p.boss_diameter / 2 - 1e-9


# ---------------------------------------------------------------------------
# hole layout
# ---------------------------------------------------------------------------
def test_hole_counts():
    p = NodeParams()
    assert len(post_bolt_positions(p)) == 2 * p.bolt_rows * p.bolts_per_row
    assert len(bracket_hole_positions(p)) == 2 * len(p.bracket_bolt_pattern)


def test_overlapping_holes_rejected():
    # bracket hole placed on top of a post bolt
    p = NodeParams()
    (leg, s, z) = post_bolt_positions(p)[0]
    bad = NodeParams(bracket_bolt_pattern=((s, z),) + p.bracket_bolt_pattern)
    with pytest.raises(ValueError, match="overlap"):
        validate_hole_layout(bad)


def test_bracket_hole_in_rib_band_rejected():
    p = NodeParams()
    z_rib = rib_levels(p)[0] + p.rib_thickness / 2
    bad = NodeParams(bracket_bolt_pattern=((0.0, max(z_rib, 9.0)),))
    with pytest.raises(ValueError, match="rib"):
        validate_hole_layout(bad)


def test_rib_levels_span_engagement():
    p = NodeParams(rib_count=3)
    levels = rib_levels(p)
    assert levels[0] == 0.0
    assert levels[-1] == pytest.approx(p.engagement_length - p.rib_thickness)
    assert len(levels) == 3


# ---------------------------------------------------------------------------
# solid
# ---------------------------------------------------------------------------
def test_default_solid_is_valid_and_watertight(default_build):
    shape = default_build.solid.val()
    assert shape.isValid()
    solids = default_build.solid.solids().vals()
    assert len(solids) == 1, "node must be one connected watertight solid"
    assert default_build.volume_mm3 > 0


def test_mass_sanity(default_build):
    # An L-node ~200 mm tall in cast steel: grams would mean a broken boolean,
    # >100 kg would mean the post prism cut failed. Wide sanity band on purpose.
    assert 2.0 < default_build.mass_kg < 60.0


def _post_envelope_intrusion(build, p):
    """Overlap volume between the casting and the post's swept volume
    (above the cast floor, if any)."""
    import cadquery as cq

    half = p.post_size / 2
    L = p.engagement_length
    z0 = (p.base_plate_thickness if p.base_plate_thickness > 0
          else -p.boss_height - L)
    probe = (
        cq.Workplane("XY")
        .transformed(offset=(0, 0, z0 + 1e-3))
        .box(2 * half - 1e-3, 2 * half - 1e-3, 4 * L,
             centered=(True, True, False))
    )
    overlap = build.solid.intersect(probe)
    solids = overlap.solids().vals()
    return sum(s.Volume() for s in solids) if solids else 0.0


def test_nothing_intrudes_into_post_envelope(default_build):
    """Core guarantee: casting must stay clear of the post's swept volume."""
    vol = _post_envelope_intrusion(default_build, NodeParams())
    assert vol < 1.0, f"casting intrudes {vol:.3f} mm^3 into the post envelope"


def test_floor_variant_seats_post_without_intrusion():
    """base_plate_thickness > 0: floor fills the footprint below the post,
    envelope above the floor stays clear, and hole/floor clashes raise."""
    p = NodeParams(base_plate_thickness=12.0)
    build = build_node(p, with_fillets=False)
    assert build.solid.val().isValid()
    assert _post_envelope_intrusion(build, p) < 1.0
    base = build_node(NodeParams(), with_fillets=False)
    assert build.volume_mm3 > base.volume_mm3  # the floor added metal
    with pytest.raises(ValueError, match="floor"):
        # lowest bolt row at z=60 with r=6: a 55 mm floor clashes
        validate_hole_layout(NodeParams(base_plate_thickness=55.0))


def test_holes_actually_cut(default_build):
    p_no_rod = NodeParams(rod_hole_diameter=10.0)  # smaller hole -> more metal
    build_small_hole = build_node(p_no_rod, with_fillets=False)
    base = build_node(NodeParams(), with_fillets=False)
    assert build_small_hole.volume_mm3 > base.volume_mm3


def test_determinism():
    a = build_node(NodeParams(), with_fillets=False)
    b = build_node(NodeParams(), with_fillets=False)
    assert a.volume_mm3 == b.volume_mm3


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(rib_count=0),
        dict(rib_count=3, bracket_bolt_pattern=((-38.0, 70.0), (38.0, 70.0))),
        dict(bolt_rows=2, bolts_per_row=3, bolt_gauge=35.0),
        dict(wall_thickness=18.0, boss_diameter=64.0,
             bracket_bolt_pattern=((-36.0, 60.0), (36.0, 60.0))),
        dict(post_gauge_mil=54, post_size=100.0, bolt_gauge=24.0,
             bracket_bolt_pattern=((-15.0, 30.0), (15.0, 30.0), (0.0, 170.0))),
        dict(boss_height=0.0),
        dict(end_rib_thickness=10.0),
    ],
)
def test_parametric_variants_build(kwargs):
    result = build_node(NodeParams(**kwargs), with_fillets=False)
    assert result.solid.val().isValid()
    assert len(result.solid.solids().vals()) == 1
    assert result.volume_mm3 > 0


def test_spine_split_variant():
    """Architecture E half: valid single solid, nothing beyond the flat
    mating plane, spine bolt holes present."""
    import cadquery as cq
    from node_kit.geometry import _spine_frame, spine_bolt_positions

    p = NodeParams(spine_split=True, boss_diameter=64.0,
                   bracket_bolt_pattern=((-30.0, 45.0), (30.0, 45.0),
                                         (-30.0, 155.0), (30.0, 155.0)))
    b = build_node(p, with_fillets=False)
    assert b.solid.val().isValid()
    assert len(b.solid.solids().vals()) == 1
    assert len(spine_bolt_positions(p)) == 2 * p.spine_bolt_rows

    (sx, sy), n, d = _spine_frame(p)
    probe = (cq.Workplane("XY").box(300, 800, 700)
             .rotate((0, 0, 0), (0, 0, 1), 45)
             .translate((sx + n[0] * 150.05, sy + n[1] * 150.05, 100)))
    ov = b.solid.intersect(probe)
    vol = sum(s.Volume() for s in ov.solids().vals()) if ov.solids().vals() else 0
    assert vol < 1.0, "material found beyond the mating plane"

    no_holes = build_node(p, with_fillets=False, with_holes="cast_only")
    assert no_holes.volume_mm3 > b.volume_mm3  # spine+bolt holes cut metal

    with pytest.raises(ValueError, match="strip"):
        NodeParams(spine_split=True, boss_diameter=52.0)  # strip too narrow


def test_exports(tmp_path, default_build):
    step = export_step(default_build, str(tmp_path / "n.step"))
    stl = export_stl(default_build, str(tmp_path / "n.stl"))
    import meshio

    mesh = meshio.read(stl)
    assert len(mesh.points) > 100
    assert (tmp_path / "n.step").stat().st_size > 10_000
