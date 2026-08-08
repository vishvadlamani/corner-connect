"""Milestone 5 tests: castability checks.

Fast analytic tests plus two mesh-based integration tests on the real
as-cast node (holes suppressed): the undercut screen must PASS for the
declared diagonal parting and FAIL for a vertical pull (the rib flanges
overhang), and the hot-spot screen must find the heavy corner region.
"""

import numpy as np
import pytest

import cadquery as cq

from node_kit.params import NodeParams, CastabilityLimits
from node_kit.geometry import build_node, export_stl, export_step
from node_kit.checks import castability as C


# ---------------------------------------------------------------------------
# analytic
# ---------------------------------------------------------------------------
def test_min_wall_detects_thin_member():
    p = NodeParams()
    res = C.min_wall_check(p)
    assert res.passed  # default: thinnest is rib at 10 >= 8
    thin = NodeParams(rib_thickness=5.0)
    res2 = C.min_wall_check(thin)
    assert not res2.passed and "rib" in res2.detail


def test_section_ratio():
    p = NodeParams()
    res = C.section_ratio_check(p)
    # default: boss annulus (52-22)/2 = 15 vs wall 12 -> 1.25, passes at 2.0
    assert res.passed and res.value == pytest.approx(15.0 / 12.0)
    fat = NodeParams(corner_web_thickness=30.0)
    assert not C.section_ratio_check(fat).passed


def test_draft_check():
    assert C.draft_check(NodeParams()).passed          # 2.0 >= 1.5
    assert not C.draft_check(NodeParams(draft_angle=0.5)).passed


def test_fillet_check():
    p = NodeParams()
    assert C.fillet_check(p, True, 8.0).passed
    assert not C.fillet_check(p, False, 0.0).passed
    assert not C.fillet_check(p, True, 3.0).passed     # below 6 mm minimum


# ---------------------------------------------------------------------------
# undercut screen on synthetic shapes
# ---------------------------------------------------------------------------
def _stl_arrays(solid, tmp_path, name):
    import meshio
    path = str(tmp_path / f"{name}.stl")
    cq.exporters.export(solid, path, exportType="STL", tolerance=0.2)
    m = meshio.read(path)
    return m.points, m.cells_dict["triangle"]


def test_undercut_box_passes_any_pull(tmp_path):
    box = cq.Workplane("XY").box(30, 20, 10)
    pts, tris = _stl_arrays(box, tmp_path, "box")
    for pull in ([0, 0, 1.0], [1.0, 1.0, 0]):
        d = np.array(pull, dtype=float)
        d /= np.linalg.norm(d)
        res = C.undercut_check(pts, tris, pull=d, cell=1.0)
        assert res.passed, res.detail


def test_undercut_spool_fails_vertical_pull(tmp_path):
    # flange-stem-flange: mold material between the flanges is trapped for a
    # vertical pull (a single flange would be fine - parting at its face)
    spool = (
        cq.Workplane("XY").circle(12).extrude(5)
        .union(cq.Workplane("XY").workplane(offset=5).circle(5).extrude(20))
        .union(cq.Workplane("XY").workplane(offset=25).circle(12).extrude(5))
    )
    pts, tris = _stl_arrays(spool, tmp_path, "spool")
    res = C.undercut_check(pts, tris, pull=np.array([0.0, 0.0, 1.0]), cell=0.8)
    assert not res.passed, res.detail
    # and it IS moldable with a horizontal pull (axis on the parting plane)
    res2 = C.undercut_check(pts, tris, pull=np.array([1.0, 0.0, 0.0]), cell=0.8)
    assert res2.passed, res2.detail


# ---------------------------------------------------------------------------
# integration on the real as-cast node
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def as_cast(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("ascast")
    p = NodeParams()
    build = build_node(p, with_fillets=False, with_holes=False)
    import meshio
    stl = str(tmp / "ascast.stl")
    export_stl(build, stl)
    m = meshio.read(stl)
    step = str(tmp / "ascast.step")
    export_step(build, step)
    return p, m.points, m.cells_dict["triangle"], step


def test_node_moldable_on_declared_diagonal_parting(as_cast):
    p, pts, tris, _ = as_cast
    res = C.undercut_check(pts, tris, cell=3.0)   # declared diagonal pull
    assert res.passed, f"declared parting is invalid: {res.detail}"


def test_node_not_moldable_on_vertical_pull(as_cast):
    """The rib flanges overhang the leg faces: a vertical pull must be
    rejected - this proves the screen discriminates, not rubber-stamps."""
    p, pts, tris, _ = as_cast
    res = C.undercut_check(pts, tris, pull=np.array([0.0, 0.0, 1.0]), cell=3.0)
    assert not res.passed


def test_hot_spot_finds_heavy_corner(as_cast):
    from node_kit.mesh import step_to_tet_mesh

    p, _, _, step = as_cast
    mesh = step_to_tet_mesh(step, size_min=5.0, size_max=9.0)
    res = C.hot_spot_check(mesh, p)
    # the boss/corner region is intentionally heavy: the estimate must land
    # well above the nominal wall and below the whole part scale
    assert p.wall_thickness < res.value < 90.0
    assert "at (" in res.detail


def test_run_castability_aggregate():
    p = NodeParams()
    build = build_node(p)
    out = C.run_castability(p, build.fillets_applied,
                            build.fillet_radius_used)
    names = [c.name for c in out["checks"]]
    assert names == ["min_wall", "section_ratio", "draft", "fillet"]
    flat = {}
    for c in out["checks"]:
        flat.update(c.as_flat_dict())
    assert "cast_min_wall_pass" in flat
