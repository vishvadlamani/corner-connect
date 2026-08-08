"""Parametric solid model of the L-shaped cast corner node (as-machined).

Coordinate system
-----------------
* Post axis = global Z; the CFS box post outer square is centred on the
  origin, faces at x,y = +/- post_size/2.
* The node wraps the +X face ("leg A") and +Y face ("leg B") of the post.
* z = 0 at the bottom of the node body; the body runs to z = engagement_length.
  Boss collars (if boss_height > 0) protrude below 0 and above engagement_length
  at the tie-rod corner only.

What this module builds
-----------------------
The AS-MACHINED part: all functional faces at final dimension, all holes at
final diameter, no draft, no shrink, no machining allowance. The foundry
pattern (shrink + allowance + draft + blind/undersize holes) is derived from
the same parameter set in pattern.py.

Topology (all booleans, no magic numbers - everything from NodeParams):
  union:
    leg A   - vertical plate on the +X post face, full face width
    leg B   - vertical plate on the +Y post face, full face width
    boss    - vertical cylinder at the corner, tangent to the post corner
              point, carrying the tie-rod through-hole; collars top/bottom
    corner  - square infill block tying legs to boss (extent set by
              corner_web_thickness beyond the boss centre)
    ribs    - rib_count horizontal stiffener plates on the leg outer faces
              plus a matching collar ring around the boss
  minus:
    post envelope (guarantees the node can never intrude into the post)
    tie-rod through-hole
    post bolt holes (bolt_rows x bolts_per_row per leg, axes normal to faces)
    bracket bolt holes (bracket_bolt_pattern per leg outer face)

Design decisions requiring engineer review (also listed in README):
  * Bracket bolt holes pass straight through the leg wall; in the real joint
    they must also pass through the CFS post wall behind (adds clamping,
    shares holes) or be replaced by tapped blind holes. Modelled as through
    holes here.
  * The boss is tangent to the post corner point; the tie rod centreline sits
    rod_center_offset = boss_radius/sqrt(2) diagonally beyond the corner.
  * Fillets are attempted on vertical edges only (v1); top/bottom convex
    edges are left sharp in the as-machined model and eased at pattern stage.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import cadquery as cq

from .params import NodeParams

# STL tessellation fidelity for exports (not a design parameter).
STL_LINEAR_DEFLECTION_MM = 0.1
STL_ANGULAR_TOLERANCE_RAD = 0.3


# ---------------------------------------------------------------------------
# Hole layout (shared with mesh.py and the AISI checks - single source)
# ---------------------------------------------------------------------------
def post_bolt_positions(p: NodeParams) -> list[tuple[str, float, float]]:
    """Post-connection bolt centres as (leg, s, z).

    ``leg`` is "A" (+X face) or "B" (+Y face); ``s`` is the in-face horizontal
    coordinate measured from the post face centreline (s == y on leg A,
    s == x on leg B); ``z`` from node bottom.
    """
    rows = p.bolt_rows
    per = p.bolts_per_row
    z0 = p.engagement_length / 2 - (rows - 1) * p.bolt_pitch / 2
    s0 = -(per - 1) * p.bolt_gauge / 2
    out = []
    for leg in ("A", "B"):
        for i in range(rows):
            for j in range(per):
                out.append((leg, s0 + j * p.bolt_gauge, z0 + i * p.bolt_pitch))
    return out


def bracket_hole_positions(p: NodeParams) -> list[tuple[str, float, float]]:
    """Bracket bolt centres as (leg, s, z), same convention as above."""
    return [(leg, x, z) for leg in ("A", "B") for (x, z) in p.bracket_bolt_pattern]


def validate_hole_layout(p: NodeParams) -> None:
    """Reject layouts where holes overlap each other or land inside a rib band.

    Raises ValueError with a specific message. Overlap criterion: centre
    distance must exceed the sum of the two hole radii (zero ligament is
    already a casting/machining defect; AISI minimum-spacing rules are
    enforced separately in checks/aisi_s100.py).
    """
    per_leg: dict[str, list[tuple[float, float, float]]] = {"A": [], "B": []}
    for (leg, s, z) in post_bolt_positions(p):
        per_leg[leg].append((s, z, p.bolt_diameter / 2))
    for (leg, s, z) in bracket_hole_positions(p):
        per_leg[leg].append((s, z, p.bracket_bolt_diameter / 2))

    for leg, holes in per_leg.items():
        for i in range(len(holes)):
            for j in range(i + 1, len(holes)):
                s1, z1, r1 = holes[i]
                s2, z2, r2 = holes[j]
                d = math.hypot(s1 - s2, z1 - z2)
                if d <= r1 + r2:
                    raise ValueError(
                        f"holes overlap on leg {leg}: ({s1},{z1}) r{r1} vs "
                        f"({s2},{z2}) r{r2}, centre distance {d:.1f}"
                    )
    # bracket holes must not pierce a rib band (bracket plate needs a flat seat)
    for (leg, s, z) in bracket_hole_positions(p):
        r = p.bracket_bolt_diameter / 2
        for z0 in rib_levels(p):
            if z + r > z0 and z - r < z0 + p.rib_thickness:
                raise ValueError(
                    f"bracket hole at z={z} on leg {leg} intersects rib band "
                    f"[{z0}, {z0 + p.rib_thickness}]"
                )


def rib_levels(p: NodeParams) -> list[float]:
    """Z of the bottom face of each rib. rib_count>=2 puts ribs at both edges."""
    L, t, n = p.engagement_length, p.rib_thickness, p.rib_count
    if n <= 0:
        return []
    if n == 1:
        return [(L - t) / 2]
    step = (L - t) / (n - 1)
    return [i * step for i in range(n)]


# ---------------------------------------------------------------------------
# Solid construction
# ---------------------------------------------------------------------------
@dataclass
class BuildResult:
    solid: cq.Workplane
    fillets_applied: bool
    fillet_radius_used: float
    volume_mm3: float
    mass_kg: float
    warnings: list[str] = field(default_factory=list)


def _box(x0, x1, y0, y1, z0, z1) -> cq.Workplane:
    return (
        cq.Workplane("XY")
        .transformed(offset=((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2))
        .box(x1 - x0, y1 - y0, z1 - z0)
    )


def _cyl_z(cx, cy, r, z0, z1) -> cq.Workplane:
    return (
        cq.Workplane("XY")
        .transformed(offset=(cx, cy, z0))
        .circle(r)
        .extrude(z1 - z0)
    )


def build_node(p: NodeParams, with_fillets: bool = True) -> BuildResult:
    """Build the as-machined node solid. Deterministic for identical params."""
    validate_hole_layout(p)

    half = p.post_size / 2
    t = p.wall_thickness
    L = p.engagement_length
    bh = p.boss_height
    cx, cy = p.rod_center_xy
    boss_r = p.boss_diameter / 2
    warnings: list[str] = []

    # --- positive volumes -------------------------------------------------
    leg_a = _box(half, half + t, -half, half + t, 0, L)
    leg_b = _box(-half, half + t, half, half + t, 0, L)
    boss = _cyl_z(cx, cy, boss_r, -bh, L + bh)
    corner_ext = p.rod_center_offset + p.corner_web_thickness
    corner = _box(half, half + corner_ext, half, half + corner_ext, 0, L)

    body = leg_a.union(leg_b).union(corner).union(boss)

    for z0 in rib_levels(p):
        z1 = z0 + p.rib_thickness
        rib_a = _box(half + t, half + t + p.rib_depth, -half, half + t, z0, z1)
        rib_b = _box(-half, half + t, half + t, half + t + p.rib_depth, z0, z1)
        ring = _cyl_z(cx, cy, boss_r + p.rib_depth, z0, z1)
        body = body.union(rib_a).union(rib_b).union(ring)

    # --- post envelope guarantee ------------------------------------------
    # Nothing of the casting may occupy the post's swept volume.
    post_prism = _box(-half - p.post_size, half, -half - p.post_size, half,
                      -bh - L, 2 * L + bh)
    # (oversized in -x/-y/z so the cut face is exactly the post face planes)
    body = body.cut(post_prism)

    # --- holes --------------------------------------------------------------
    body = body.cut(_cyl_z(cx, cy, p.rod_hole_diameter / 2,
                           -bh - L, 2 * L + bh))

    reach = t + p.rib_depth + 1.0  # 1 mm overshoot purely for robust booleans

    def _cut_face_hole(bod, leg: str, s: float, z: float, dia: float):
        r = dia / 2
        if leg == "A":
            hole = (
                cq.Workplane("YZ")
                .transformed(offset=(s, z, half - 1.0))
                .circle(r)
                .extrude(reach + 2.0)
            )
        else:
            hole = (
                cq.Workplane("XZ")
                .transformed(offset=(s, z, -(half + reach + 1.0)))
                .circle(r)
                .extrude(reach + 2.0)
            )
        return bod.cut(hole)

    for (leg, s, z) in post_bolt_positions(p):
        body = _cut_face_hole(body, leg, s, z, p.bolt_diameter)
    for (leg, s, z) in bracket_hole_positions(p):
        body = _cut_face_hole(body, leg, s, z, p.bracket_bolt_diameter)

    # --- fillets (vertical edges only, v1) ----------------------------------
    fillets_applied = False
    radius_used = 0.0
    if with_fillets and p.fillet_radius > 0:
        body, fillets_applied, radius_used, fw = _try_fillets(body, p)
        warnings.extend(fw)

    solid = body
    vol = solid.val().Volume()
    mass = vol * p.casting_material.density
    return BuildResult(
        solid=solid,
        fillets_applied=fillets_applied,
        fillet_radius_used=radius_used,
        volume_mm3=vol,
        mass_kg=mass,
        warnings=warnings,
    )


def _vertical_edges(body: cq.Workplane, p: NodeParams,
                    exclude_cylinders: bool) -> list:
    """Straight vertical edges eligible for filleting.

    Always excluded:
      * the reentrant post-corner pocket edge (x = y = post_size/2) - a
        fillet there would add material inside the post envelope;
      * non-vertical and curved edges.
    With exclude_cylinders=True, also drops edges lying on the boss or
    rod-hole cylindrical surfaces (their seam edges make OCCT fillets fail;
    the cost is losing the boss-to-leg junction fillets in that pass).
    """
    half = p.post_size / 2
    cx, cy = p.rod_center_xy
    boss_r = p.boss_diameter / 2
    rod_r = p.rod_hole_diameter / 2
    tol = 1e-6

    def selectable(edge) -> bool:
        try:
            v0, v1 = edge.Vertices()
        except (TypeError, ValueError):
            return False
        a, b = v0.toTuple(), v1.toTuple()
        if abs(a[0] - b[0]) > tol or abs(a[1] - b[1]) > tol:
            return False
        if abs(a[2] - b[2]) < tol:
            return False
        if abs(a[0] - half) < 1e-3 and abs(a[1] - half) < 1e-3:
            return False
        if exclude_cylinders:
            r = math.hypot(a[0] - cx, a[1] - cy)
            if abs(r - boss_r) < 0.5 or abs(r - rod_r) < 0.5:
                return False
        return True

    return [e for e in body.edges().vals() if selectable(e)]


def _try_fillets(body: cq.Workplane, p: NodeParams):
    """Staged fillet attempts, most complete first. Never raises.

    Returns (body, applied, radius_used, warnings).
    """
    warnings: list[str] = []
    stages = [
        (p.fillet_radius, False),      # everything incl. boss junctions
        (p.fillet_radius, True),       # planar feature edges only
        (p.fillet_radius / 2, True),   # smaller radius, planar edges only
    ]
    for radius, exclude_cyl in stages:
        edges = _vertical_edges(body, p, exclude_cylinders=exclude_cyl)
        if not edges:
            break
        try:
            filleted = body.newObject(edges).fillet(radius)
            if not filleted.val().isValid():
                raise ValueError("fillet produced an invalid solid")
            if exclude_cyl:
                warnings.append(
                    "boss-junction fillets skipped (cylinder-edge fillet "
                    "failed); junctions are sharp in this build"
                )
            if radius != p.fillet_radius:
                warnings.append(
                    f"fillet radius reduced to {radius:.1f} mm to succeed"
                )
            return filleted, True, radius, warnings
        except Exception as exc:  # OCCT fillet failures are opaque
            warnings.append(
                f"fillet pass (r={radius:.1f}, exclude_cyl={exclude_cyl}) "
                f"failed: {type(exc).__name__}"
            )
    warnings.append("geometry built WITHOUT fillets; castability will flag")
    return body, False, 0.0, warnings


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------
def export_step(result: BuildResult, path: str) -> str:
    cq.exporters.export(result.solid, path, exportType="STEP")
    return path


def export_stl(result: BuildResult, path: str,
               linear_deflection: float = STL_LINEAR_DEFLECTION_MM,
               angular_tolerance: float = STL_ANGULAR_TOLERANCE_RAD) -> str:
    cq.exporters.export(
        result.solid, path, exportType="STL",
        tolerance=linear_deflection, angularTolerance=angular_tolerance,
    )
    return path


def node_summary(p: NodeParams, result: BuildResult) -> dict:
    bb = result.solid.val().BoundingBox()
    return {
        "volume_mm3": result.volume_mm3,
        "mass_kg": result.mass_kg,
        "bbox_x_mm": bb.xlen,
        "bbox_y_mm": bb.ylen,
        "bbox_z_mm": bb.zlen,
        "fillets_applied": result.fillets_applied,
        "fillet_radius_used": result.fillet_radius_used,
        "n_post_bolts": len(post_bolt_positions(p)),
        "n_bracket_holes": len(bracket_hole_positions(p)),
        "warnings": result.warnings,
    }
