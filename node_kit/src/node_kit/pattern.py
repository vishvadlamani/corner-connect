"""Foundry pattern export (Milestone 8).

From one NodeParams candidate this module produces:
  * node_pattern.step / .stl   - the FOUNDRY PATTERN: machining allowance
    added on functional faces, drilled holes omitted (cast solid), the
    cored rod hole cast UNDERSIZE, everything scaled by (1 + shrink_factor)
    for cast-steel patternmaker's shrink;
  * node_machined.step         - the as-machined reference part;
  * print pieces               - the pattern at 1:1 for FDM/SLA printing on
    a build volume (default 256 mm cube); if it exceeds the volume it is
    auto-split at a mid-plane normal to the longest axis with alignment
    dowel pins/holes added at the interface;
  * pattern_summary.json       - allowances, scale, bounding boxes, volumes.

ALLOWANCE MODEL (documented approximation, v1): the allowance is applied
by REBUILDING the parametric solid with padded dimensions rather than by
per-face offsetting -
    post_size          -> post_size - 2a   (post-bearing inner faces +a)
    wall_thickness     -> wall + 2a        (bracket outer faces +a)
    engagement_length  -> engagement + 2a, shifted -a  (top/bottom +a)
    boss_diameter      -> boss + 2a        (collar OD +a)
    rod_hole_diameter  -> rod - 2a         (cored hole cast undersize)
Known deviations, flagged for the patternmaker: rib flanks carry no
allowance (non-functional as-cast faces); on a spine_split half the mating
plane derives from the boss radius, so its allowance is ~0.71a rather
than a - state a on the pattern drawing and let the machinist confirm
stock. Draft is NOT modelled on the pattern solid; the declared
draft_angle is recorded in the summary for the patternmaker to apply on
pull-parallel faces (standard practice for printed patterns is to add
draft in the slicer/CAD of record).

Units: mm. Deterministic.
"""

from __future__ import annotations

import dataclasses
import json
import pathlib

import cadquery as cq

from .params import NodeParams
from .geometry import build_node, BuildResult


def pattern_params(p: NodeParams) -> NodeParams:
    """Padded parameter set implementing the allowance model above."""
    a = p.machining_allowance
    return dataclasses.replace(
        p,
        post_size=p.post_size - 2 * a,
        wall_thickness=p.wall_thickness + 2 * a,
        engagement_length=p.engagement_length + 2 * a,
        boss_diameter=p.boss_diameter + 2 * a,
        rod_hole_diameter=max(p.rod_hole_diameter - 2 * a, 2 * a),
    )


def _scale(wp: cq.Workplane, factor: float) -> cq.Workplane:
    """Uniform scale about the origin (patternmaker's shrink).

    Uses the exact gp_Trsf conformal-scale path; transformGeometry
    (gp_GTrsf) re-approximates analytic surfaces and was measured to
    distort the volume by ~0.24 % on this part."""
    from OCP.gp import gp_Trsf, gp_Pnt
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform

    t = gp_Trsf()
    t.SetScale(gp_Pnt(0, 0, 0), factor)
    out = []
    for obj in wp.vals():
        new = BRepBuilderAPI_Transform(obj.wrapped, t, True).Shape()
        out.append(cq.Shape.cast(new))
    return cq.Workplane("XY").newObject(out)


def build_pattern(p: NodeParams) -> tuple[cq.Workplane, dict]:
    """The shrink-scaled foundry pattern solid + metadata."""
    pp = pattern_params(p)
    raw = build_node(pp, with_fillets=True, with_holes="cast_only")
    # re-centre the engagement padding: +a stock below z=0 as well as above
    a = p.machining_allowance
    shifted = raw.solid.translate((0, 0, -a))
    scaled = _scale(shifted, 1.0 + p.shrink_factor)
    vol = scaled.val().Volume()
    meta = {
        "machining_allowance_mm": a,
        "shrink_factor": p.shrink_factor,
        "scale": 1.0 + p.shrink_factor,
        "draft_angle_deg_to_apply": p.draft_angle,
        "rod_hole_cast_diameter_mm":
            pattern_params(p).rod_hole_diameter * (1 + p.shrink_factor),
        "pattern_volume_mm3": vol,
        "as_cast_mass_estimate_kg": raw.volume_mm3 * p.casting_material.density,
        "fillets_applied": raw.fillets_applied,
        "warnings": raw.warnings,
    }
    return scaled, meta


# ---------------------------------------------------------------------------
# print splitting
# ---------------------------------------------------------------------------
def _bbox(wp: cq.Workplane):
    bb = wp.val().BoundingBox()
    return bb


def split_for_printing(solid: cq.Workplane, build_volume: float = 256.0,
                       dowel_d: float = 6.0, dowel_len: float = 8.0,
                       clearance: float = 0.2) -> list[cq.Workplane]:
    """Return printable pieces. If the solid fits the cubic build volume,
    [solid] unchanged; otherwise split at the mid-plane normal to the
    longest axis and add alignment dowels (pins on piece A, matching holes
    with diametral clearance on piece B)."""
    bb = _bbox(solid)
    spans = {"x": bb.xlen, "y": bb.ylen, "z": bb.zlen}
    if all(v <= build_volume for v in spans.values()):
        return [solid]

    axis = max(spans, key=spans.get)
    mid = {"x": (bb.xmin + bb.xmax) / 2,
           "y": (bb.ymin + bb.ymax) / 2,
           "z": (bb.zmin + bb.zmax) / 2}[axis]
    big = 4 * max(spans.values())

    def halfspace(sign):
        off = {"x": (mid + sign * big / 2, (bb.ymin + bb.ymax) / 2,
                     (bb.zmin + bb.zmax) / 2),
               "y": ((bb.xmin + bb.xmax) / 2, mid + sign * big / 2,
                     (bb.zmin + bb.zmax) / 2),
               "z": ((bb.xmin + bb.xmax) / 2, (bb.ymin + bb.ymax) / 2,
                     mid + sign * big / 2)}[axis]
        return (cq.Workplane("XY").box(big, big, big)
                .translate(off))

    piece_a = solid.cut(halfspace(+1))
    piece_b = solid.cut(halfspace(-1))

    # dowels at two interior points of the cut section
    pts = _section_points(solid, axis, mid)
    for (u, v) in pts:
        centre = {"x": (mid, u, v), "y": (u, mid, v), "z": (u, v, mid)}[axis]
        ax_map = {"x": (1, 0, 0), "y": (0, 1, 0), "z": (0, 0, 1)}[axis]
        pin = _cyl_along(centre, ax_map, dowel_d / 2, dowel_len)
        hole = _cyl_along(centre, ax_map, (dowel_d + clearance) / 2,
                          dowel_len + clearance)
        piece_a = piece_a.union(pin)
        piece_b = piece_b.cut(hole)
    return [piece_a, piece_b]


def _cyl_along(centre, axis, r, half_len):
    cyl = cq.Workplane("XY").circle(r).extrude(2 * half_len).translate(
        (0, 0, -half_len))
    if axis == (1, 0, 0):
        cyl = cyl.rotate((0, 0, 0), (0, 1, 0), 90)
    elif axis == (0, 1, 0):
        cyl = cyl.rotate((0, 0, 0), (1, 0, 0), -90)
    return cyl.translate(centre)


def _section_points(solid: cq.Workplane, axis: str, mid: float,
                    n: int = 2) -> list[tuple[float, float]]:
    """A few points interior to the solid on the cut plane, found by
    probing a coarse grid with small-box intersections."""
    bb = _bbox(solid)
    if axis == "x":
        u0, u1, v0, v1 = bb.ymin, bb.ymax, bb.zmin, bb.zmax
    elif axis == "y":
        u0, u1, v0, v1 = bb.xmin, bb.xmax, bb.zmin, bb.zmax
    else:
        u0, u1, v0, v1 = bb.xmin, bb.xmax, bb.ymin, bb.ymax
    found = []
    for fu in (0.3, 0.45, 0.6, 0.75):
        for fv in (0.3, 0.5, 0.7):
            u = u0 + fu * (u1 - u0)
            v = v0 + fv * (v1 - v0)
            centre = {"x": (mid, u, v), "y": (u, mid, v),
                      "z": (u, v, mid)}[axis]
            probe = (cq.Workplane("XY").box(6, 6, 6).translate(centre))
            inter = solid.intersect(probe)
            vols = inter.solids().vals()
            if vols and sum(s.Volume() for s in vols) > 0.95 * 216:
                found.append((u, v))
                if len(found) >= n:
                    return found
    return found


# ---------------------------------------------------------------------------
# top-level export
# ---------------------------------------------------------------------------
def export_pattern(p: NodeParams, out_dir: str = "runs/pattern",
                   build_volume: float = 256.0) -> dict:
    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    pattern, meta = build_pattern(p)
    cq.exporters.export(pattern, str(out / "node_pattern.step"),
                        exportType="STEP")
    cq.exporters.export(pattern, str(out / "node_pattern.stl"),
                        exportType="STL", tolerance=0.1, angularTolerance=0.3)

    machined = build_node(p)
    cq.exporters.export(machined.solid, str(out / "node_machined.step"),
                        exportType="STEP")

    pieces = split_for_printing(pattern, build_volume=build_volume)
    piece_files = []
    for i, piece in enumerate(pieces):
        name = ("node_pattern_print.stl" if len(pieces) == 1
                else f"node_pattern_print_piece{i + 1}.stl")
        cq.exporters.export(piece, str(out / name), exportType="STL",
                            tolerance=0.1, angularTolerance=0.3)
        piece_files.append(name)

    bb = _bbox(pattern)
    meta.update({
        "pattern_bbox_mm": [bb.xlen, bb.ylen, bb.zlen],
        "build_volume_mm": build_volume,
        "print_pieces": piece_files,
        "as_machined_mass_kg": machined.mass_kg,
    })
    (out / "pattern_summary.json").write_text(json.dumps(meta, indent=2))
    return meta
