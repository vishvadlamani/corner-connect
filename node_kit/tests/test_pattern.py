"""Milestone 8 tests: foundry pattern exports."""

import json

import pytest

from node_kit.params import NodeParams
from node_kit.geometry import build_node
from node_kit.pattern import (
    pattern_params, build_pattern, split_for_printing, export_pattern,
)


def test_pattern_params_padding():
    p = NodeParams()
    pp = pattern_params(p)
    a = p.machining_allowance
    assert pp.post_size == p.post_size - 2 * a          # inner faces +a
    assert pp.wall_thickness == p.wall_thickness + 2 * a
    assert pp.engagement_length == p.engagement_length + 2 * a
    assert pp.rod_hole_diameter == p.rod_hole_diameter - 2 * a  # undersize


def test_pattern_bigger_than_machined_and_scaled():
    p = NodeParams()
    pattern, meta = build_pattern(p)
    machined = build_node(p)
    v_pat = pattern.val().Volume()
    # allowance stock + omitted drilled holes + shrink scale > machined
    assert v_pat > machined.volume_mm3
    # scale check: rebuild unscaled by dividing out the cubic factor
    unscaled = v_pat / (1 + p.shrink_factor) ** 3
    raw = build_node(pattern_params(p), with_fillets=True,
                     with_holes="cast_only")
    assert unscaled == pytest.approx(raw.volume_mm3, rel=1e-6)
    assert meta["scale"] == pytest.approx(1.02)


def test_split_not_needed_when_fits():
    p = NodeParams()
    pattern, _ = build_pattern(p)
    pieces = split_for_printing(pattern, build_volume=256.0)
    assert len(pieces) == 1


def test_split_with_dowels_when_oversize():
    p = NodeParams()
    pattern, _ = build_pattern(p)
    # force the split path with an artificially small build volume
    pieces = split_for_printing(pattern, build_volume=150.0)
    assert len(pieces) == 2
    va = sum(s.Volume() for s in pieces[0].solids().vals())
    vb = sum(s.Volume() for s in pieces[1].solids().vals())
    v0 = pattern.val().Volume()
    # pins add to A, clearance holes subtract from B; total is close to v0
    assert va + vb == pytest.approx(v0, rel=0.02)
    assert va != pytest.approx(vb, rel=1e-3)  # pins/holes differ


def test_export_pattern_files(tmp_path):
    p = NodeParams(spine_split=True, boss_diameter=64.0,
                   bracket_bolt_pattern=((-30.0, 45.0), (30.0, 45.0),
                                         (-30.0, 155.0), (30.0, 155.0)))
    meta = export_pattern(p, out_dir=str(tmp_path))
    for f in ("node_pattern.step", "node_pattern.stl",
              "node_machined.step", "pattern_summary.json"):
        assert (tmp_path / f).exists()
    summary = json.loads((tmp_path / "pattern_summary.json").read_text())
    assert summary["scale"] == pytest.approx(1.02)
    assert summary["print_pieces"]
    assert summary["rod_hole_cast_diameter_mm"] < p.rod_hole_diameter