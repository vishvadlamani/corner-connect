"""Geometric manufacturability checks for the sand-cast node — Milestone 5.

Two tiers of check, both headless and deterministic:

ANALYTIC (parameter-level) — exact, instant, run on every optimiser
candidate:
  * minimum wall thickness over every parametric member
  * adjacent-section thickness ratios (shrinkage porosity risk)
  * declared draft angle vs the foundry minimum
  * fillet radius vs minimum AND whether the build actually applied fillets

MESH (as-cast form, holes suppressed) — resolution-limited screens:
  * moldability/undercut: voxel-column parity along the declared pull axis.
    For a two-part mold pulled along +/-d, any column (line parallel to d)
    that intersects the solid in MORE THAN ONE interval traps mold material
    -> undercut or core required. Holes are suppressed because they are
    cast blind/undersize or drilled (pattern.py decides), so they must not
    count as mold features.
  * hot spots: max inscribed sphere estimated as the largest
    distance-to-surface over interior nodes of a tet mesh. Resolution:
    underestimates the true inscribed radius by up to ~1 element size —
    stated in the result.

Parting declaration (params.CastabilityLimits.parting = "diagonal"): the
mold parts on the plane x = y through the corner; halves pull along
+/- (1, 1, 0)/sqrt(2). The undercut screen VERIFIES this choice instead of
trusting it.

Every check returns a CheckResult; run_castability() aggregates them.
Units: mm throughout.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ..params import NodeParams, CastabilityLimits, DEFAULT_CASTABILITY


@dataclass
class CheckResult:
    name: str
    passed: bool
    value: float          # measured quantity (units per check)
    limit: float          # the threshold it was compared against
    detail: str = ""

    def as_flat_dict(self) -> dict:
        return {
            f"cast_{self.name}_pass": self.passed,
            f"cast_{self.name}_value": self.value,
            f"cast_{self.name}_limit": self.limit,
        }


# ---------------------------------------------------------------------------
# analytic checks
# ---------------------------------------------------------------------------
def member_thicknesses(p: NodeParams) -> dict[str, float]:
    """Every distinct cast wall in the parametric design, by name."""
    th = {
        "leg_wall": p.wall_thickness,
        "rib": p.rib_thickness,
        "boss_annulus": (p.boss_diameter - p.rod_hole_diameter) / 2.0,
        "corner_web": p.corner_web_thickness,
    }
    if p.end_rib_thickness > 0:
        th["end_rib"] = p.end_rib_thickness
    return th


def min_wall_check(p: NodeParams,
                   limits: CastabilityLimits = DEFAULT_CASTABILITY) -> CheckResult:
    th = member_thicknesses(p)
    worst = min(th, key=th.get)
    return CheckResult(
        "min_wall", th[worst] >= limits.min_wall_mm, th[worst],
        limits.min_wall_mm, f"thinnest member: {worst}",
    )


def section_ratio_check(p: NodeParams,
                        limits: CastabilityLimits = DEFAULT_CASTABILITY) -> CheckResult:
    """Adjacent-section ratio: junctions where a thick member feeds a thin
    one cause shrinkage porosity in the thick side (it solidifies last).
    Pairs checked are the physical junctions of this topology."""
    th = member_thicknesses(p)
    pairs = [("leg_wall", "rib"), ("leg_wall", "boss_annulus"),
             ("leg_wall", "corner_web")]
    if "end_rib" in th:
        pairs.append(("leg_wall", "end_rib"))
    worst_pair, worst = None, 1.0
    for a, b in pairs:
        r = max(th[a], th[b]) / min(th[a], th[b])
        if r > worst:
            worst, worst_pair = r, (a, b)
    return CheckResult(
        "section_ratio", worst <= limits.max_adjacent_ratio, worst,
        limits.max_adjacent_ratio, f"worst junction: {worst_pair}",
    )


def draft_check(p: NodeParams,
                limits: CastabilityLimits = DEFAULT_CASTABILITY) -> CheckResult:
    return CheckResult(
        "draft", p.draft_angle >= limits.min_draft_deg, p.draft_angle,
        limits.min_draft_deg,
        "declared pattern draft (applied at pattern stage, pattern.py)",
    )


def fillet_check(p: NodeParams, fillets_applied: bool,
                 fillet_radius_used: float,
                 limits: CastabilityLimits = DEFAULT_CASTABILITY) -> CheckResult:
    ok = fillets_applied and fillet_radius_used >= limits.min_fillet_mm
    return CheckResult(
        "fillet", ok, fillet_radius_used if fillets_applied else 0.0,
        limits.min_fillet_mm,
        "" if ok else "fillets missing or below minimum in the built solid",
    )


# ---------------------------------------------------------------------------
# mesh checks
# ---------------------------------------------------------------------------
def _pull_direction(limits: CastabilityLimits) -> np.ndarray:
    if limits.parting == "diagonal":
        return np.array([1.0, 1.0, 0.0]) / math.sqrt(2.0)
    raise ValueError(f"unknown parting declaration {limits.parting!r}")


def _rotation_to_z(d: np.ndarray) -> np.ndarray:
    """Rotation matrix taking unit vector d to +z."""
    d = d / np.linalg.norm(d)
    z = np.array([0.0, 0.0, 1.0])
    v = np.cross(d, z)
    c = float(d @ z)
    if np.linalg.norm(v) < 1e-12:
        return np.eye(3) if c > 0 else np.diag([1.0, -1.0, -1.0])
    vx = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + vx + vx @ vx * (1.0 / (1.0 + c))


def solid_intervals_per_column(points: np.ndarray, triangles: np.ndarray,
                               pull: np.ndarray, cell: float):
    """Voxel-column parity scan of a watertight triangle mesh.

    Rotates the mesh so `pull` is +z, lays a (cell x cell) grid over the
    footprint, casts a +z ray through each cell centre and counts surface
    crossings; crossings come in pairs, each pair = one solid interval.

    Returns (n_columns_hit, n_multi_interval_columns, multi_cells_xy).
    """
    R = _rotation_to_z(pull)
    pts = points @ R.T
    tri = pts[triangles]                       # (n, 3, 3)

    # skip triangles nearly parallel to the pull: they are grazing surface,
    # not transversal crossings, and their projected slivers only add parity
    # noise (proper crossings on such walls belong to the adjacent facets)
    n_vec = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    n_norm = np.linalg.norm(n_vec, axis=1) + 1e-30
    transversal = np.abs(n_vec[:, 2] / n_norm) > 0.02

    lo = tri[:, :, :2].min(axis=1)
    hi = tri[:, :, :2].max(axis=1)
    # deterministic irrational jitter avoids columns aligned with mesh edges
    gmin = pts[:, :2].min(axis=0) - cell * (1.0 + 0.2937)
    n_hit = 0
    multi = []
    # accumulate crossing counts per cell
    crossings: dict[tuple[int, int], int] = {}
    for k in range(len(tri)):
        if not transversal[k]:
            continue
        a, b, c = tri[k]
        i0, j0 = np.floor((lo[k] - gmin) / cell).astype(int)
        i1, j1 = np.ceil((hi[k] - gmin) / cell).astype(int)
        for i in range(i0, i1 + 1):
            for j in range(j0, j1 + 1):
                px = gmin[0] + (i + 0.5) * cell
                py = gmin[1] + (j + 0.5) * cell
                # 2D point-in-triangle (projected)
                if _point_in_tri_2d(px, py, a, b, c):
                    crossings[(i, j)] = crossings.get((i, j), 0) + 1
    for key, n in crossings.items():
        if n < 2:
            continue          # grazing artifact
        n_hit += 1
        if n >= 4:            # 2 intervals or more
            multi.append(key)
    return n_hit, len(multi), multi


def _point_in_tri_2d(px, py, a, b, c, eps=1e-9) -> bool:
    # strictly interior: boundary hits are dropped (the jittered grid makes
    # exact-edge alignment measure-zero; strictness avoids double counting
    # a crossing shared by two adjacent triangles)
    d1 = (px - b[0]) * (a[1] - b[1]) - (a[0] - b[0]) * (py - b[1])
    d2 = (px - c[0]) * (b[1] - c[1]) - (b[0] - c[0]) * (py - c[1])
    d3 = (px - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (py - a[1])
    return (d1 > eps and d2 > eps and d3 > eps) or \
           (d1 < -eps and d2 < -eps and d3 < -eps)


def undercut_check(points: np.ndarray, triangles: np.ndarray,
                   limits: CastabilityLimits = DEFAULT_CASTABILITY,
                   pull: np.ndarray | None = None,
                   cell: float = 3.0,
                   tolerance_fraction: float = 0.005) -> CheckResult:
    """Undercut / core-required screen relative to the declared parting.

    Run on the AS-CAST form (holes suppressed). A small fraction of
    multi-interval columns (< tolerance_fraction) is tolerated as
    rasterisation noise at tangencies; anything above it means the mold
    cannot part along the declared axis without cores.
    """
    if pull is None:
        pull = _pull_direction(limits)
    n_hit, n_multi, _ = solid_intervals_per_column(points, triangles, pull, cell)
    frac = n_multi / max(n_hit, 1)
    return CheckResult(
        "undercut", frac <= tolerance_fraction, frac, tolerance_fraction,
        f"{n_multi}/{n_hit} columns multi-interval along pull "
        f"{np.round(pull, 3).tolist()} at {cell} mm grid",
    )


def hot_spot_check(mesh, p: NodeParams,
                   limits: CastabilityLimits = DEFAULT_CASTABILITY) -> CheckResult:
    """Isolated heavy-section screen via inscribed-sphere estimate.

    mesh: meshio tet mesh of the AS-CAST form. The largest
    distance-to-surface over interior nodes approximates the maximum
    inscribed sphere radius (underestimates by up to ~1 element size).
    Limit: inscribed DIAMETER <= max_inscribed_ratio * nominal wall.
    """
    from scipy.spatial import cKDTree
    from ..mesh import volume_cells, boundary_faces

    cells = volume_cells(mesh, "tetra10")
    surf_nodes = set()
    for (_, _, tri) in boundary_faces(cells):
        surf_nodes.update(tri)
    all_nodes = np.unique(cells[:, :4])        # corner nodes are enough
    interior = np.array([n for n in all_nodes if n not in surf_nodes])
    if len(interior) == 0:
        return CheckResult("hot_spot", True, 0.0,
                           limits.max_inscribed_ratio * p.wall_thickness,
                           "no interior nodes at this mesh resolution")
    tree = cKDTree(mesh.points[list(surf_nodes)])
    d, _ = tree.query(mesh.points[interior])
    r_max = float(d.max())
    loc = mesh.points[interior[int(d.argmax())]]
    dia = 2.0 * r_max
    limit = limits.max_inscribed_ratio * p.wall_thickness
    return CheckResult(
        "hot_spot", dia <= limit, dia, limit,
        f"max inscribed sphere dia ~{dia:.1f} mm at "
        f"({loc[0]:.0f}, {loc[1]:.0f}, {loc[2]:.0f}); "
        "resolution-limited estimate (underestimates)",
    )


# ---------------------------------------------------------------------------
# aggregate
# ---------------------------------------------------------------------------
def run_castability(
    p: NodeParams,
    fillets_applied: bool,
    fillet_radius_used: float,
    as_cast_stl_points: np.ndarray | None = None,
    as_cast_stl_triangles: np.ndarray | None = None,
    as_cast_tet_mesh=None,
    limits: CastabilityLimits = DEFAULT_CASTABILITY,
) -> dict:
    """All castability checks. Mesh-based screens run only when the caller
    supplies the as-cast surface/tet meshes (objective.py does; unit tests
    may skip them). Returns {'checks': [CheckResult...], 'all_pass': bool}.
    """
    checks = [
        min_wall_check(p, limits),
        section_ratio_check(p, limits),
        draft_check(p, limits),
        fillet_check(p, fillets_applied, fillet_radius_used, limits),
    ]
    if as_cast_stl_points is not None and as_cast_stl_triangles is not None:
        checks.append(undercut_check(
            as_cast_stl_points, as_cast_stl_triangles, limits))
    if as_cast_tet_mesh is not None:
        checks.append(hot_spot_check(as_cast_tet_mesh, p, limits))
    return {"checks": checks, "all_pass": all(c.passed for c in checks)}
