"""gmsh meshing utilities: benchmark geometries and STEP -> tet meshes.

All meshers are deterministic: fixed gmsh algorithm choices, single thread,
fixed random seed. Every function returns a meshio.Mesh in meshio canonical
node ordering (which matches Abaqus/CalculiX ordering for tetra10 and
triangle6, so fea.py can emit connectivity verbatim).

Milestone 3 scope: benchmark meshes for the three validation gates plus the
generic STEP -> C3D10 path. The full node+post+bolts assembly builder lands
with Milestone 6 (it reuses these pieces and fea.py's contact machinery,
which gate 3 exercises first on the lap-shear coupon).
"""

from __future__ import annotations

import contextlib
import math
import tempfile
import pathlib

import gmsh
import meshio
import numpy as np


@contextlib.contextmanager
def _gmsh_session():
    gmsh.initialize()
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.option.setNumber("General.NumThreads", 1)
        gmsh.option.setNumber("Mesh.RandomSeed", 1)
        gmsh.option.setNumber("Mesh.Algorithm", 6)     # Frontal-Delaunay 2D
        gmsh.option.setNumber("Mesh.Algorithm3D", 1)   # Delaunay 3D
        gmsh.option.setNumber("Mesh.Optimize", 1)
        gmsh.option.setNumber("Mesh.ElementOrder", 2)
        gmsh.option.setNumber("Mesh.SecondOrderLinear", 1)
        yield
    finally:
        gmsh.finalize()


def _to_meshio() -> meshio.Mesh:
    """Extract the current gmsh model into a meshio.Mesh via a temp file."""
    with tempfile.TemporaryDirectory() as td:
        path = str(pathlib.Path(td) / "mesh.msh")
        gmsh.option.setNumber("Mesh.MshFileVersion", 4.1)
        gmsh.write(path)
        return meshio.read(path)


def cantilever_beam_mesh(length: float, depth: float, width: float,
                         elem_size: float) -> meshio.Mesh:
    """Rectangular cantilever: x in [0, L], y in [-b/2, b/2], z in [-h/2, h/2].

    Quadratic tets (tetra10 -> C3D10). Fixed face at x=0, load face at x=L.
    """
    with _gmsh_session():
        gmsh.model.add("cantilever")
        gmsh.model.occ.addBox(0, -width / 2, -depth / 2, length, width, depth)
        gmsh.model.occ.synchronize()
        gmsh.option.setNumber("Mesh.MeshSizeMin", elem_size)
        gmsh.option.setNumber("Mesh.MeshSizeMax", elem_size)
        gmsh.model.mesh.generate(3)
        return _to_meshio()


def plate_with_hole_2d(half_length: float, half_width: float,
                       hole_radius: float, far_size: float,
                       hole_size: float) -> meshio.Mesh:
    """Quarter model of a plate with a central circular hole (plane stress).

    Quarter occupies [0, half_length] x [0, half_width] with the quarter-hole
    at the origin. Symmetry edges: x=0 and y=0. Load edge: x=half_length.
    Quadratic triangles (triangle6 -> CPS6).
    """
    with _gmsh_session():
        gmsh.model.add("plate_hole")
        occ = gmsh.model.occ
        rect = occ.addRectangle(0, 0, 0, half_length, half_width)
        disk = occ.addDisk(0, 0, 0, hole_radius, hole_radius)
        occ.cut([(2, rect)], [(2, disk)])
        occ.synchronize()
        # refine at the hole
        f_dist = gmsh.model.mesh.field.add("Distance")
        gmsh.model.mesh.field.setNumbers(
            f_dist, "CurvesList",
            [c[1] for c in gmsh.model.getEntities(1)])
        # distance from all curves overshoots; simpler: threshold on radius
        f_ball = gmsh.model.mesh.field.add("Ball")
        gmsh.model.mesh.field.setNumber(f_ball, "Radius", 3 * hole_radius)
        gmsh.model.mesh.field.setNumber(f_ball, "VIn", hole_size)
        gmsh.model.mesh.field.setNumber(f_ball, "VOut", far_size)
        gmsh.model.mesh.field.setNumber(f_ball, "XCenter", 0)
        gmsh.model.mesh.field.setNumber(f_ball, "YCenter", 0)
        gmsh.model.mesh.field.setAsBackgroundMesh(f_ball)
        gmsh.option.setNumber("Mesh.MeshSizeExtendFromBoundary", 0)
        gmsh.model.mesh.generate(2)
        return _to_meshio()


def lap_coupon_mesh(sheet_w: float, sheet_len: float, sheet_t: float,
                    hole_d: float, hole_cx: float,
                    pin_d: float, pin_len: float,
                    fine: float, coarse: float) -> meshio.Mesh:
    """Single-bolt bearing coupon: thin sheet with a hole + a separate pin.

    Sheet: x in [0, sheet_len], y in [-sheet_w/2, +sheet_w/2],
    z in [0, sheet_t]; hole centre at (hole_cx, 0). The grip end (fixed) is
    the far face x = sheet_len. Pin: vertical cylinder centred in the hole,
    z in [-(pin_len - sheet_t)/2, sheet_t + (pin_len - sheet_t)/2], with a
    diametral clearance (hole_d - pin_d).

    Two disconnected volumes on purpose - fea.py ties them with contact.
    Quadratic tets.
    """
    with _gmsh_session():
        gmsh.model.add("coupon")
        occ = gmsh.model.occ
        sheet = occ.addBox(0, -sheet_w / 2, 0, sheet_len, sheet_w, sheet_t)
        hole = occ.addCylinder(hole_cx, 0, -1, 0, 0, sheet_t + 2, hole_d / 2)
        occ.cut([(3, sheet)], [(3, hole)])
        over = (pin_len - sheet_t) / 2
        occ.addCylinder(hole_cx, 0, -over, 0, 0, pin_len, pin_d / 2)
        occ.synchronize()

        f_ball = gmsh.model.mesh.field.add("Ball")
        gmsh.model.mesh.field.setNumber(f_ball, "Radius", 2.5 * hole_d)
        gmsh.model.mesh.field.setNumber(f_ball, "VIn", fine)
        gmsh.model.mesh.field.setNumber(f_ball, "VOut", coarse)
        gmsh.model.mesh.field.setNumber(f_ball, "XCenter", hole_cx)
        gmsh.model.mesh.field.setNumber(f_ball, "YCenter", 0)
        gmsh.model.mesh.field.setNumber(f_ball, "ZCenter", sheet_t / 2)
        gmsh.model.mesh.field.setAsBackgroundMesh(f_ball)
        gmsh.option.setNumber("Mesh.MeshSizeExtendFromBoundary", 0)
        gmsh.model.mesh.generate(3)
        return _to_meshio()


def post_shell_mesh(mid_size: float, z0: float, z1: float,
                    elem_size: float) -> meshio.Mesh:
    """CFS box post as a shell MIDSURFACE mesh: the 4 lateral walls of a
    square prism (side = mid_size = post outer size - design thickness),
    centred on the origin, from z0 to z1. Quadratic triangles -> ccx S6
    shells; ccx expands them by the real thickness given on the
    *SHELL SECTION card, so contact acts at the true outer surface.
    """
    with _gmsh_session():
        gmsh.model.add("post_shell")
        occ = gmsh.model.occ
        h = mid_size / 2
        box = occ.addBox(-h, -h, z0, 2 * h, 2 * h, z1 - z0)
        occ.synchronize()
        vol = [(3, box)]
        # drop the volume and the two horizontal caps, keep 4 lateral walls
        caps = []
        for (dim, tag) in gmsh.model.getEntities(2):
            _, _, zmin, _, _, zmax = gmsh.model.getBoundingBox(dim, tag)
            if abs(zmax - zmin) < 1e-6:
                caps.append((dim, tag))
        gmsh.model.occ.remove(vol)
        gmsh.model.occ.remove(caps)
        occ.synchronize()
        gmsh.option.setNumber("Mesh.MeshSizeMin", elem_size)
        gmsh.option.setNumber("Mesh.MeshSizeMax", elem_size)
        gmsh.model.mesh.generate(2)
        return _to_meshio()


def surface_cells(mesh: meshio.Mesh, cell_type: str = "triangle6") -> np.ndarray:
    blocks = [b.data for b in mesh.cells if b.type == cell_type]
    if not blocks:
        raise ValueError(f"mesh has no {cell_type} cells")
    return np.vstack(blocks)


def shell_normals(points: np.ndarray, cells: np.ndarray) -> np.ndarray:
    """Unit normal per shell element (from the first three nodes)."""
    a, b, c = points[cells[:, 0]], points[cells[:, 1]], points[cells[:, 2]]
    n = np.cross(b - a, c - a)
    return n / (np.linalg.norm(n, axis=1, keepdims=True) + 1e-30)


def step_to_tet_mesh(step_path: str, size_min: float,
                     size_max: float) -> meshio.Mesh:
    """Generic STEP -> quadratic tet mesh (used for the node casting)."""
    with _gmsh_session():
        gmsh.model.add("step_import")
        gmsh.model.occ.importShapes(step_path)
        gmsh.model.occ.synchronize()
        gmsh.option.setNumber("Mesh.MeshSizeMin", size_min)
        gmsh.option.setNumber("Mesh.MeshSizeMax", size_max)
        gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 20)
        gmsh.model.mesh.generate(3)
        return _to_meshio()


# ---------------------------------------------------------------------------
# mesh interrogation helpers (shared by fea.py deck builders)
# ---------------------------------------------------------------------------
def nodes_where(mesh: meshio.Mesh, predicate) -> np.ndarray:
    """0-based indices of nodes whose (x, y, z) satisfies predicate."""
    pts = mesh.points
    keep = [i for i in range(len(pts)) if predicate(*pts[i])]
    return np.asarray(keep, dtype=int)


def volume_cells(mesh: meshio.Mesh, cell_type: str = "tetra10") -> np.ndarray:
    blocks = [b.data for b in mesh.cells if b.type == cell_type]
    if not blocks:
        raise ValueError(f"mesh has no {cell_type} cells")
    return np.vstack(blocks)


# Abaqus/CalculiX C3D10 face definitions by local corner-node indices
# (0-based): F1=(0,1,2), F2=(0,3,1), F3=(1,3,2), F4=(2,3,0).
TET_FACES = {1: (0, 1, 2), 2: (0, 3, 1), 3: (1, 3, 2), 4: (2, 3, 0)}


def boundary_faces(cells: np.ndarray) -> list[tuple[int, int, tuple[int, int, int]]]:
    """External faces of a tet mesh as (element_index, face_id, corner_nodes).

    A face is external when it appears in exactly one element.
    """
    seen: dict[tuple[int, ...], tuple[int, int, tuple[int, int, int]]] = {}
    dropped: set[tuple[int, ...]] = set()
    for ei, conn in enumerate(cells):
        for fid, (a, b, c) in TET_FACES.items():
            tri = (int(conn[a]), int(conn[b]), int(conn[c]))
            key = tuple(sorted(tri))
            if key in seen:
                del seen[key]
                dropped.add(key)
            elif key not in dropped:
                seen[key] = (ei, fid, tri)
    return list(seen.values())


def faces_where(mesh: meshio.Mesh, cells: np.ndarray, predicate,
                cell_type: str = "tetra10") -> list[tuple[int, int]]:
    """(element_index, face_id) for external faces whose corner nodes ALL
    satisfy predicate(x, y, z)."""
    pts = mesh.points
    out = []
    for (ei, fid, tri) in boundary_faces(cells):
        if all(predicate(*pts[n]) for n in tri):
            out.append((ei, fid))
    return out
