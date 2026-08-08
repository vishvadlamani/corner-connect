"""One candidate -> full evaluation -> results-contract rows (Milestone 6).

Pipeline per candidate:
  geometry (as-machined + as-cast) -> castability screens -> assembly FEA
  (5 load cases) -> AISI checks with bolt-group demands -> one row per
  (candidate, load case) into a Parquet file.

ASSEMBLY FEA MODEL (simplifications documented in README):
  * Node casting: C3D10 tets from the as-machined STEP.
  * Post: S6 shell elements on the box midsurface at the CFS design
    thickness (captures wall bending/tilting flexibility). Shell is
    UNPERFORATED - post-side hole bearing capacity is carried by the AISI
    checks, not the FEA.
  * Bolts: per bolt, the casting hole rim is a rigid body about a
    reference node; the post side is a distributing coupling over a shell
    node patch; the two reference nodes are connected by three SPRING2
    elements with beam-derived stiffnesses (axial EA/L, shear 12EI/L^3).
    Bolt forces are recovered exactly as k * relative displacement.
  * Contact: frictionless surface-to-surface, linear pressure-overclosure,
    casting inner faces (master) vs post shell outer faces (slave).
  * Linear kinematics (small displacement); contact iterations only.

LOAD CASE MECHANICS (Architecture B "stack" assumed - worst case for the
casting; under Architecture A "clamp" LC1 vanishes into post continuity):
  LC1  stacked gravity as bearing pressure on the top collar annulus,
       reacted by the bottom collar annulus (casting column path).
  LC2  rod uplift: +z at all bracket hole rims, reacted at the top collar
       annulus (the rod nut).
  LC3  floor-beam end reaction: VERTICAL (-z) force at the face-A bracket
       rims, reacted through bolts+contact into the post (conservative:
       no bottom bearing assist). NOTE the LoadCase field shear_x_N holds
       this magnitude; "x" identifies WHICH beam (the one spanning X),
       the physical force is gravity shear (-z).
  LC4  mirror of LC3 on face B.
  LC5  LC1 pressure + LC3 force + facade force on face B, with both the
       bearing path and the bolt path active.

Units: mm, N, MPa.
"""

from __future__ import annotations

import json
import math
import pathlib
import subprocess
import time
from dataclasses import dataclass

import meshio
import numpy as np
import pandas as pd

from .params import NodeParams, LoadCase, default_load_cases, DESIGN_METHOD
from .geometry import (
    build_node, export_step, export_stl,
    post_bolt_positions, bracket_hole_positions,
)
from .mesh import (
    step_to_tet_mesh, post_shell_mesh, volume_cells, surface_cells,
    shell_normals, nodes_where, faces_where,
)
from .fea import (
    Deck, ElementBlock, MaterialDef, Section, Step,
    write_inp, run_ccx, parse_frd,
)
from .checks import aisi_s100 as aisi
from .checks import castability as cast


@dataclass
class AssemblySizes:
    node_min: float = 5.0
    node_max: float = 11.0
    shell_size: float = 9.0
    post_extend: float = 150.0     # shell length beyond the node each way
    cast_tet_min: float = 5.0      # as-cast mesh for hot-spot screen
    cast_tet_max: float = 10.0


COARSE = AssemblySizes(node_min=8.0, node_max=16.0, shell_size=13.0,
                       post_extend=100.0, cast_tet_min=7.0, cast_tet_max=14.0)

CONTACT_K = 5.0e4          # MPa/mm, linear pressure-overclosure slope
RIM_TOL = 0.8              # mm, node-capture tolerance on hole cylinders
PATCH_RADIUS_FACTOR = 1.6  # shell coupling patch radius, x bolt diameter


# ---------------------------------------------------------------------------
# assembly deck construction
# ---------------------------------------------------------------------------
class AssemblyModel:
    """Holds the merged mesh, id maps and named sets for one candidate."""

    def __init__(self, p: NodeParams, sizes: AssemblySizes, workdir: str):
        self.p = p
        self.sizes = sizes
        self.workdir = pathlib.Path(workdir)
        self.workdir.mkdir(parents=True, exist_ok=True)
        self._build()

    def _build(self):
        p, sizes = self.p, self.sizes
        half = p.post_size / 2
        t = p.wall_thickness
        L = p.engagement_length
        bh = p.boss_height
        cx, cy = p.rod_center_xy

        # --- meshes -------------------------------------------------------
        build = build_node(p, with_fillets=True, with_holes=True)
        self.build = build
        step = export_step(build, str(self.workdir / "node.step"))
        node_mesh = step_to_tet_mesh(step, sizes.node_min, sizes.node_max)
        self.node_cells = volume_cells(node_mesh, "tetra10")
        n_node_pts = len(node_mesh.points)

        # shell midsurface sized so the post OUTER surface sits fit_clearance
        # away from the casting inner faces (real sliding-fit assembly gap;
        # contact engages only where prying closes it)
        mid = p.post_size - p.post_thickness - 2.0 * p.fit_clearance
        zp0, zp1 = -sizes.post_extend, L + sizes.post_extend
        shell_mesh = post_shell_mesh(mid, zp0, zp1, sizes.shell_size)
        self.shell_cells_local = surface_cells(shell_mesh, "triangle6")

        self.points = np.vstack([node_mesh.points, shell_mesh.points])
        self.shell_cells = self.shell_cells_local + n_node_pts
        self.n_node_pts = n_node_pts
        self.node_mesh = node_mesh
        self.shell_mesh = shell_mesh
        self.mid_half = mid / 2
        self.zp = (zp0, zp1)

        # element id ranges: casting first, shells second
        self.n_cast_el = len(self.node_cells)
        self.n_shell_el = len(self.shell_cells)
        next_eid = self.n_cast_el + self.n_shell_el + 1
        next_nid = len(self.points) + 1

        # --- casting inner faces (contact master) --------------------------
        tol = 0.05
        faces_a = faces_where(node_mesh, self.node_cells,
                              lambda x, y, z: abs(x - half) < tol)
        faces_b = faces_where(node_mesh, self.node_cells,
                              lambda x, y, z: abs(y - half) < tol)
        self.surf_cast_inner = [(ei + 1, fid) for (ei, fid) in faces_a + faces_b]

        # --- shell contact faces (slave): +x and +y walls, outward side ----
        normals = shell_normals(self.points, self.shell_cells)
        cent = self.points[self.shell_cells[:, :3]].mean(axis=1)
        self.surf_shell_outer = []
        for k in range(len(self.shell_cells)):
            wall_a = abs(cent[k][0] - self.mid_half) < 1.0
            wall_b = abs(cent[k][1] - self.mid_half) < 1.0
            if not (wall_a or wall_b):
                continue
            outward = np.array([1.0, 0.0, 0.0]) if wall_a else np.array([0.0, 1.0, 0.0])
            side = "SPOS" if normals[k] @ outward > 0 else "SNEG"
            self.surf_shell_outer.append((self.n_cast_el + k + 1, side))

        # --- per-bolt attachments ------------------------------------------
        self.extra_nodes: dict[int, tuple] = {}
        self.nsets: dict[str, np.ndarray] = {}
        self.raw_nsets: dict[str, list[int]] = {}
        self.rigid_bodies: list[tuple] = []
        self.dist_couplings: list[tuple] = []
        self.springs: list[tuple] = []   # (elset, dof1, dof2, k, eid, n1, n2)
        self.bolt_refs: list[dict] = []

        d = p.bolt_diameter
        r_bolt = d / 2
        A_b = math.pi * r_bolt ** 2
        I_b = math.pi * d ** 4 / 64.0
        L_spring = (t / 2) + (half - self.mid_half)   # refA to refB gap
        E_bolt = p.bolt_material.E
        k_ax = E_bolt * A_b / L_spring
        k_sh = 12.0 * E_bolt * I_b / L_spring ** 3

        pts = node_mesh.points
        spts = shell_mesh.points

        for i, (leg, s, z) in enumerate(post_bolt_positions(p)):
            if leg == "A":
                axis = 1  # x
                rim_pred = lambda x, y, zz: (half - 0.2 <= x <= half + t + 0.2
                                             and abs(math.hypot(y - s, zz - z) - r_bolt) < RIM_TOL)
                refA_xyz = (half + t / 2, s, z)
                refB_xyz = (self.mid_half, s, z)
                patch_pred = lambda x, y, zz: (abs(x - self.mid_half) < 1.0 and
                                               math.hypot(y - s, zz - z) < PATCH_RADIUS_FACTOR * d)
            else:
                axis = 2  # y
                rim_pred = lambda x, y, zz: (half - 0.2 <= y <= half + t + 0.2
                                             and abs(math.hypot(x - s, zz - z) - r_bolt) < RIM_TOL)
                refA_xyz = (s, half + t / 2, z)
                refB_xyz = (s, self.mid_half, z)
                patch_pred = lambda x, y, zz: (abs(y - self.mid_half) < 1.0 and
                                               math.hypot(x - s, zz - z) < PATCH_RADIUS_FACTOR * d)

            rim = np.array([j for j in range(len(pts)) if rim_pred(*pts[j])])
            patch_local = np.array([j for j in range(len(spts))
                                    if patch_pred(*spts[j])])
            if len(rim) < 6:
                raise RuntimeError(f"bolt {i}: only {len(rim)} rim nodes captured")
            if len(patch_local) < 3:
                raise RuntimeError(f"bolt {i}: only {len(patch_local)} patch nodes")

            refA, rotA, refB = next_nid, next_nid + 1, next_nid + 2
            next_nid += 3
            self.extra_nodes[refA] = refA_xyz
            self.extra_nodes[rotA] = refA_xyz
            self.extra_nodes[refB] = refB_xyz
            self.nsets[f"BRIM{i}"] = rim
            self.nsets[f"BPATCH{i}"] = patch_local + self.n_node_pts
            self.raw_nsets[f"BREFA{i}"] = [refA]
            self.rigid_bodies.append((f"BRIM{i}", refA, rotA))
            self.dist_couplings.append(
                (f"DC{i}", next_eid, refB, f"BPATCH{i}"))
            next_eid += 1
            for dof in (1, 2, 3):
                k = k_ax if dof == axis else k_sh
                self.springs.append(
                    (f"SPR{i}D{dof}", dof, dof, k, next_eid, refA, refB))
                next_eid += 1
            self.bolt_refs.append(
                {"i": i, "leg": leg, "s": s, "z": z, "axis": axis,
                 "refA": refA, "refB": refB, "k": (k_ax, k_sh)})

        # --- bracket hole rims ---------------------------------------------
        self.bracket_rims: list[dict] = []
        rb = p.bracket_bolt_diameter / 2
        for i, (leg, s, z) in enumerate(bracket_hole_positions(p)):
            if leg == "A":
                pred = lambda x, y, zz: (half + 0.1 < x < half + t - 0.1 + 0.2
                                         and abs(math.hypot(y - s, zz - z) - rb) < RIM_TOL)
            else:
                pred = lambda x, y, zz: (half + 0.1 < y < half + t - 0.1 + 0.2
                                         and abs(math.hypot(x - s, zz - z) - rb) < RIM_TOL)
            rim = np.array([j for j in range(len(pts)) if pred(*pts[j])])
            if len(rim) < 6:
                raise RuntimeError(f"bracket hole {i}: {len(rim)} rim nodes")
            self.nsets[f"KRIM{i}"] = rim
            self.bracket_rims.append({"i": i, "leg": leg, "rim": rim})

        # --- support/measurement sets ---------------------------------------
        boss_r = p.boss_diameter / 2
        z_top = L + bh
        z_bot = -bh
        self.nsets["COLLARTOP"] = nodes_where(
            node_mesh, lambda x, y, z: abs(z - z_top) < 0.05
            and math.hypot(x - cx, y - cy) <= boss_r + 0.5)
        self.nsets["COLLARBOT"] = nodes_where(
            node_mesh, lambda x, y, z: abs(z - z_bot) < 0.05
            and math.hypot(x - cx, y - cy) <= boss_r + 0.5)
        self.faces_collar_top = [
            (ei + 1, fid) for (ei, fid) in faces_where(
                node_mesh, self.node_cells,
                lambda x, y, z: abs(z - z_top) < 0.05
                and math.hypot(x - cx, y - cy) <= boss_r + 0.5)]

        shell_off = self.n_node_pts
        self.nsets["POSTBOT"] = shell_off + nodes_where(
            shell_mesh, lambda x, y, z: abs(z - self.zp[0]) < 0.05)
        self.nsets["POSTTOP"] = shell_off + nodes_where(
            shell_mesh, lambda x, y, z: abs(z - self.zp[1]) < 0.05)

        # collar top face area (linear-triangle approximation)
        area = 0.0
        for (eid, fid) in self.faces_collar_top:
            conn = self.node_cells[eid - 1]
            from .mesh import TET_FACES
            tri = [conn[j] for j in TET_FACES[fid]]
            a, b, c = (self.points[tri[0]], self.points[tri[1]],
                       self.points[tri[2]])
            area += 0.5 * np.linalg.norm(np.cross(b - a, c - a))
        self.collar_top_area = area

    # -----------------------------------------------------------------
    def deck_for(self, lc: LoadCase) -> Deck:
        p = self.p
        deck = Deck(
            points=self.points,
            blocks=[
                ElementBlock("CASTING", "C3D10", self.node_cells),
                ElementBlock("POST", "S6", self.shell_cells),
            ],
            nsets=dict(self.nsets),
            raw_nsets=dict(self.raw_nsets),
            extra_nodes=dict(self.extra_nodes),
            materials=[
                MaterialDef("CAST", p.casting_material.E, p.casting_material.nu),
                MaterialDef("CFS", p.post_material.E, p.post_material.nu),
            ],
            sections=[
                Section("CASTING", "CAST"),
                Section("POST", "CFS", thickness=p.post_thickness, kind="shell"),
            ],
            surfaces={
                "SCASTIN": self.surf_cast_inner,
                "SPOSTOUT": self.surf_shell_outer,
            },
            rigid_bodies=list(self.rigid_bodies),
            dist_couplings=list(self.dist_couplings),
            interactions=[("CI", CONTACT_K)],
            contact_pairs=[("CI", "SPOSTOUT", "SCASTIN")],
            springs=list(self.springs),
        )

        boundaries = [("POSTBOT", 1, 3, 0.0), ("POSTTOP", 1, 2, 0.0)]
        cloads: list[tuple] = []
        dloads: list[tuple] = []

        def rim_loads(rims, total, dof, sign):
            per_hole = total / len(rims)
            for r in rims:
                per_node = sign * per_hole / len(r["rim"])
                for nid in r["rim"]:
                    cloads.append((int(nid) + 1, dof, per_node))

        face_a_rims = [r for r in self.bracket_rims if r["leg"] == "A"]
        face_b_rims = [r for r in self.bracket_rims if r["leg"] == "B"]

        if lc.axial_N:
            pressure = lc.axial_N / self.collar_top_area
            for (eid, fid) in self.faces_collar_top:
                dloads.append((eid, fid, pressure))
            boundaries.append(("COLLARBOT", 3, 3, 0.0))
        if lc.rod_tension_N:
            rim_loads(self.bracket_rims, lc.rod_tension_N, 3, +1.0)
            boundaries.append(("COLLARTOP", 3, 3, 0.0))
        if lc.shear_x_N:
            rim_loads(face_a_rims, lc.shear_x_N, 3, -1.0)
        if lc.shear_y_N:
            rim_loads(face_b_rims, lc.shear_y_N, 3, -1.0)

        deck.steps = [Step(
            name=lc.case_id,
            static_line="1.0, 1.0, 1e-4, 1.0",
            max_increments=30,
            boundaries=boundaries,
            cloads=cloads,
            dloads=dloads,
            node_file="U",
            el_file="S",
            output_2d=True,
        )]
        return deck


# ---------------------------------------------------------------------------
# post-processing
# ---------------------------------------------------------------------------
def _von_mises(sig: np.ndarray) -> float:
    sxx, syy, szz, sxy, syz, szx = sig[:6]
    return math.sqrt(0.5 * ((sxx - syy) ** 2 + (syy - szz) ** 2 +
                            (szz - sxx) ** 2) +
                     3.0 * (sxy ** 2 + syz ** 2 + szx ** 2))


def _peak_vm(stress: dict, ids) -> float:
    vals = [_von_mises(stress[i]) for i in ids if i in stress]
    return max(vals) if vals else float("nan")


def _mean_uz(disp: dict, idx0: np.ndarray) -> float:
    vals = [disp[int(i) + 1][2] for i in idx0 if int(i) + 1 in disp]
    return float(np.mean(vals)) if vals else float("nan")


def solve_all_lcs(model: AssemblyModel, lcs: list[LoadCase]) -> dict[str, dict]:
    """Write all decks, run ccx jobs in parallel (one thread each), then
    post-process. Wall clock ~ the slowest load case."""
    import concurrent.futures as cf
    import os

    for lc in lcs:
        deck = model.deck_for(lc)
        write_inp(deck, str(model.workdir / f"lc_{lc.case_id.lower()}.inp"))

    def _run(lc):
        t0 = time.perf_counter()
        res = run_ccx(str(model.workdir), f"lc_{lc.case_id.lower()}",
                      timeout_s=3600, nthreads=1)
        return lc.case_id, res, time.perf_counter() - t0

    out = {}
    workers = max(1, min(len(lcs), os.cpu_count() or 1))
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        for cid, res, dt in ex.map(_run, lcs):
            out[cid] = (res, dt)
    return {lc.case_id: _post_lc(model, lc, *out[lc.case_id]) for lc in lcs}


def _post_lc(model: AssemblyModel, lc: LoadCase, res: dict,
             solve_s: float) -> dict:
    frd = parse_frd(res["frd"])
    disp = frd["DISP"][-1]
    stress = frd.get("STRESS", [{}])[-1]

    cast_ids = range(1, model.n_node_pts + 1)
    shell_ids = range(model.n_node_pts + 1, len(model.points) + 1)
    out = {
        "solve_s": solve_s,
        "cast_vm": _peak_vm(stress, cast_ids),
        "post_vm": _peak_vm(stress, shell_ids),
        "disp": disp,
    }

    # bolt forces from spring relative displacement (exact by construction)
    bolt_forces = []
    for b in model.bolt_refs:
        uA = np.array(disp.get(b["refA"], [0, 0, 0])[:3])
        uB = np.array(disp.get(b["refB"], [0, 0, 0])[:3])
        du = uA - uB
        k_ax, k_sh = b["k"]
        f = np.empty(3)
        for dof in range(3):
            k = k_ax if dof + 1 == b["axis"] else k_sh
            f[dof] = k * du[dof]
        ax = abs(f[b["axis"] - 1])
        sh = math.sqrt(sum(f[d] ** 2 for d in range(3) if d + 1 != b["axis"]))
        bolt_forces.append({"i": b["i"], "leg": b["leg"], "z": b["z"],
                            "tension_N": ax, "shear_N": sh})
    out["bolt_forces"] = bolt_forces

    # stiffness (N/mm) per load-case type
    F = max(abs(lc.axial_N), abs(lc.rod_tension_N),
            abs(lc.shear_x_N), abs(lc.shear_y_N))
    if lc.axial_N:
        d_meas = abs(_mean_uz(disp, model.nsets["COLLARTOP"]))
    elif lc.rod_tension_N:
        rims = np.concatenate([r["rim"] for r in model.bracket_rims])
        d_meas = abs(_mean_uz(disp, rims))
    else:
        leg = "A" if lc.shear_x_N else "B"
        rims = np.concatenate([r["rim"] for r in model.bracket_rims
                               if r["leg"] == leg])
        d_meas = abs(_mean_uz(disp, rims))
    out["stiffness_N_mm"] = F / d_meas if d_meas > 0 else float("nan")
    return out


# ---------------------------------------------------------------------------
# AISI demand mapping + contract row
# ---------------------------------------------------------------------------
def aisi_ratios(p: NodeParams, fea: dict, method: str = DESIGN_METHOD) -> dict:
    """DCRs for the governing bolt, using FEA-recovered bolt forces.

    Net-section and block-shear ratios are reported as NaN: their demand is
    membrane tension in the post wall from drag/brace forces, which are
    undefined until the owner fixes the lateral scheme (README). Factors are
    UNVERIFIED - rows are stamped factors_unverified=True.
    """
    t, d = p.post_thickness, p.bolt_diameter
    Fu_sheet = p.post_material.Fu
    Fu_bolt = p.bolt_material.Fu

    worst = max(fea["bolt_forces"], key=lambda b: b["shear_N"],
                default={"shear_N": 0.0, "tension_N": 0.0})
    V, T = worst["shear_N"], worst["tension_N"]

    pn_bear, _, _ = aisi.bolt_bearing_with_deformation(d, t, Fu_sheet)
    pn_shear, _, _ = aisi.bolt_shear(d, Fu_bolt)
    pn_t_red, _, _ = aisi.shear_tension_interaction(V, T, d, Fu_bolt)

    dcr = lambda dem, cap, ls: aisi.demand_capacity_ratio(
        dem, cap, ls, method, allow_unverified=True)
    ratios = {
        "ratio_bearing_J332": dcr(V, pn_bear, "bolt_bearing_with_deformation"),
        "ratio_bolt_shear": dcr(V, pn_shear, "bolt_shear"),
        "ratio_bolt_tension_interaction":
            dcr(T, pn_t_red, "bolt_tension") if pn_t_red > 0 else float("inf"),
        "ratio_net_section": float("nan"),
        "ratio_block_shear": float("nan"),
    }
    ok, _, _ = aisi.spacing_and_edge_limits(
        d, min(p.bolt_pitch, p.bolt_gauge), p.bolt_edge_distance,
        p.bolt_edge_distance)
    ratios["spacing_limits_pass"] = ok
    ratios["governing_bolt_V_N"] = V
    ratios["governing_bolt_T_N"] = T
    return ratios


def _git_sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"],
                              capture_output=True, text=True,
                              cwd=pathlib.Path(__file__).parent
                              ).stdout.strip()[:12]
    except Exception:
        return "unknown"


def evaluate_candidate(
    p: NodeParams,
    load_cases: list[LoadCase] | None = None,
    workdir: str = "runs/candidate",
    sizes: AssemblySizes | None = None,
    seed: int = 42,
    parquet_out: str | None = "runs/results.parquet",
) -> pd.DataFrame:
    """Full evaluation of one candidate. Returns the contract DataFrame
    (one row per load case) and optionally writes it to Parquet."""
    sizes = sizes or AssemblySizes()
    lcs = load_cases or default_load_cases()
    wd = pathlib.Path(workdir)
    wd.mkdir(parents=True, exist_ok=True)

    # castability: undercut screen on the mold form (no holes - all holes
    # are cores or drills); hot-spot screen on the pattern form (cored rod
    # hole present, drilled bolt holes absent)
    mold_form = build_node(p, with_fillets=True, with_holes="none")
    stl = export_stl(mold_form, str(wd / "as_cast.stl"))
    m = meshio.read(stl)
    pattern_form = build_node(p, with_fillets=True, with_holes="cast_only")
    step_cast = export_step(pattern_form, str(wd / "as_cast.step"))
    cast_tet = step_to_tet_mesh(step_cast, sizes.cast_tet_min,
                                sizes.cast_tet_max)
    build_machined = build_node(p)
    castab = cast.run_castability(
        p, build_machined.fillets_applied, build_machined.fillet_radius_used,
        as_cast_stl_points=m.points,
        as_cast_stl_triangles=m.cells_dict["triangle"],
        as_cast_tet_mesh=cast_tet,
    )
    cast_cols = {}
    for c in castab["checks"]:
        cast_cols.update(c.as_flat_dict())
    cast_cols["castability_all_pass"] = castab["all_pass"]

    model = AssemblyModel(p, sizes, str(wd))
    Fy_cast = p.casting_material.Fy
    Fy_post = p.post_material.Fy

    fea_by_lc = solve_all_lcs(model, lcs)
    rows = []
    for lc in lcs:
        fea = fea_by_lc[lc.case_id]
        ratios = aisi_ratios(p, fea)
        vm_ratio = fea["cast_vm"] / Fy_cast
        post_ratio = fea["post_vm"] / Fy_post

        named = {k: v for k, v in ratios.items() if k.startswith("ratio_")}
        named["ratio_casting_vm"] = vm_ratio
        named["ratio_post_wall_vm"] = post_ratio
        finite = {k: v for k, v in named.items() if np.isfinite(v)}
        governing = max(finite, key=finite.get) if finite else "none"
        lc_pass = (all(v <= 1.0 for v in finite.values())
                   and ratios["spacing_limits_pass"]
                   and cast_cols["castability_all_pass"])

        row = {}
        row.update(p.as_flat_dict())
        row.update(lc.as_flat_dict())
        row.update({
            "mass_kg": model.build.mass_kg,
            "casting_peak_vm_MPa": fea["cast_vm"],
            "post_wall_peak_vm_MPa": fea["post_vm"],
            "joint_stiffness_N_mm": fea["stiffness_N_mm"],
            **ratios,
            "ratio_casting_vm": vm_ratio,
            "ratio_post_wall_vm": post_ratio,
            **cast_cols,
            "lc_pass": lc_pass,
            "governing_limit_state": governing,
            "solve_wallclock_s": fea["solve_s"],
            "git_sha": _git_sha(),
            "timestamp_utc": pd.Timestamp.utcnow().isoformat(),
            "rng_seed": seed,
            "design_method": DESIGN_METHOD,
            "factors_unverified": True,
            "n_elements_casting": model.n_cast_el,
            "n_elements_post": model.n_shell_el,
        })
        row["bolt_forces_json"] = json.dumps(fea["bolt_forces"])
        rows.append(row)

    df = pd.DataFrame(rows)
    df["candidate_pass"] = bool(df["lc_pass"].all())
    if parquet_out:
        pathlib.Path(parquet_out).parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(parquet_out, index=False)
    return df
