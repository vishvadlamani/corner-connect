"""Single source of truth for every parameter in the pipeline.

UNITS (enforced everywhere, no exceptions):
    length      mm
    force       N
    stress      MPa (N/mm^2)
    density     kg/mm^3   (7850 kg/m^3 == 7.85e-6 kg/mm^3)
    mass        kg (reported)
    angle       degrees

No module in this package may contain a dimensioned literal that is not
defined here or derived from a value defined here.

PLACEHOLDER POLICY
------------------
Values marked ``PLACEHOLDER`` below were NOT supplied by the project owner
(load magnitudes, post size, post gauge). They are order-of-magnitude
stand-ins so the pipeline can be built and validated. Any optimisation
result produced with PLACEHOLDER values is meaningless for design and MUST
NOT be sealed. See README "Placeholders awaiting real values".
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from typing import Optional


# ---------------------------------------------------------------------------
# CFS gauge table
# ---------------------------------------------------------------------------
# SSMA / AISI standard mil designations. "mil" = thousandths of an inch of
# minimum delivered (base metal) thickness. Design thickness is the value used
# in strength calcs; delivered minimum is 95% of design thickness per
# AISI S100 (95% delivered-thickness rule; exact clause ref UNVERIFIED - to be
# confirmed against the standard in Milestone 4, see README).
# Metric conversion: design thickness [in] * 25.4, rounded to 3 decimals.
GAUGE_TABLE_MM: dict[int, dict[str, float]] = {
    # mil : {design thickness mm, minimum delivered thickness mm}
    33: {"design": 0.879, "minimum": 0.836},
    43: {"design": 1.146, "minimum": 1.087},
    54: {"design": 1.438, "minimum": 1.367},
    68: {"design": 1.811, "minimum": 1.720},
    97: {"design": 2.583, "minimum": 2.454},
}


def gauge_to_design_thickness(mil: int) -> float:
    """Map a standard mil designation (33/43/54/68/97) to design thickness, mm."""
    if mil not in GAUGE_TABLE_MM:
        raise ValueError(
            f"Unsupported gauge {mil} mil; supported: {sorted(GAUGE_TABLE_MM)}"
        )
    return GAUGE_TABLE_MM[mil]["design"]


# ---------------------------------------------------------------------------
# Materials
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Material:
    """Isotropic linear-elastic material. Stresses in MPa, density kg/mm^3."""

    name: str
    E: float          # Young's modulus, MPa
    nu: float         # Poisson's ratio
    density: float    # kg/mm^3
    Fy: float         # specified minimum yield strength, MPa
    Fu: float         # specified minimum tensile strength, MPa


# ASTM A27 Grade 65-35: Fu >= 450 MPa (65 ksi), Fy >= 240 MPa (35 ksi).
CAST_STEEL_A27_65_35 = Material(
    name="ASTM A27 Gr 65-35", E=200_000.0, nu=0.3, density=7.85e-6,
    Fy=240.0, Fu=450.0,
)

# ASTM A148 Grade 80-50: Fu >= 550 MPa (80 ksi), Fy >= 345 MPa (50 ksi).
CAST_STEEL_A148_80_50 = Material(
    name="ASTM A148 Gr 80-50", E=200_000.0, nu=0.3, density=7.85e-6,
    Fy=345.0, Fu=550.0,
)

# PLACEHOLDER: CFS sheet grade assumed 50 ksi (ASTM A1003 ST50 or equivalent).
# Confirm the actual coil grade the roll-former runs.
CFS_GRADE_50 = Material(
    name="CFS 50ksi (PLACEHOLDER grade)", E=203_000.0, nu=0.3, density=7.85e-6,
    Fy=345.0, Fu=450.0,
)

# PLACEHOLDER: bolt grade assumed ISO 8.8 / A325-class. Confirm.
BOLT_GRADE_8_8 = Material(
    name="Bolt ISO 8.8 (PLACEHOLDER grade)", E=200_000.0, nu=0.3,
    density=7.85e-6, Fy=640.0, Fu=800.0,
)


# ---------------------------------------------------------------------------
# Castability limits (sand-cast steel). Foundry-dependent norms - confirm
# with the actual foundry before sealing; values are common practice for
# small/medium steel sand castings.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CastabilityLimits:
    min_wall_mm: float = 8.0            # minimum castable wall, steel sand casting
    max_adjacent_ratio: float = 2.0     # adjacent section thickness ratio (porosity)
    min_draft_deg: float = 1.5          # minimum pattern draft on zero-draft walls
    min_fillet_mm: float = 6.0          # minimum internal fillet radius
    max_inscribed_ratio: float = 2.0    # max inscribed-sphere dia / nominal wall
    # declared parting: diagonal plane x = y through the corner, mold halves
    # pull along +/- (1,1,0)/sqrt(2). Chosen because the L is symmetric about
    # it and every functional face stays mold-formed (verified by the
    # undercut check in checks/castability.py).
    parting: str = "diagonal"


DEFAULT_CASTABILITY = CastabilityLimits()


# ---------------------------------------------------------------------------
# Node parameters
# ---------------------------------------------------------------------------
@dataclass
class NodeParams:
    """Full parametric definition of one corner-node candidate.

    Geometry convention (see geometry.py for the full picture):
      * Post axis = global Z. Post outer square centred on origin.
      * The node wraps the +X and +Y faces of the post ("leg A" on +X,
        "leg B" on +Y). z=0 is the bottom of the node body.
      * The tie-rod boss sits diagonally off the post corner at
        (+post_size/2 + c, +post_size/2 + c), c = rod_center_offset.
    """

    # ---- CFS post (PLACEHOLDER dimensions - confirm with roll-former) ----
    post_size: float = 150.0          # outer dimension of square box post, mm
    post_gauge_mil: int = 97          # 33/43/54/68/97 mil designation
    post_thickness: float = field(default=None)  # design thickness mm; derived from gauge if None

    # ---- node body ----
    engagement_length: float = 200.0  # wrap height up the post, mm
    wall_thickness: float = 12.0      # casting nominal wall (the two legs), mm
    rib_thickness: float = 10.0       # horizontal rib plate thickness, mm
    rib_count: int = 2                # number of horizontal ribs (>=2 puts one at top and bottom edge)
    rib_depth: float = 15.0           # rib protrusion beyond leg outer face, mm
    end_rib_thickness: float = 0.0    # vertical closure rib at each leg free end, mm (0 = none)
    corner_web_thickness: float = 14.0  # corner infill web extent beyond post corner, mm

    # ---- node-to-post bolts (through the CFS wall) ----
    bolt_diameter: float = 12.0       # mm
    bolt_rows: int = 3                # rows (stacked in Z) per leg
    bolts_per_row: int = 2            # bolts across the face width per row
    bolt_pitch: float = 40.0          # row-to-row spacing in Z, mm
    bolt_gauge: float = 40.0          # bolt-to-bolt spacing across the face, mm
    bolt_edge_distance: float = 25.0  # centre-to-edge in the CFS sheet, mm

    # ---- tie rod ----
    rod_hole_diameter: float = 22.0   # clearance hole for the continuous rod, mm
    boss_diameter: float = 52.0       # OD of the vertical corner boss, mm
    boss_height: float = 5.0          # raised collar above top face / below bottom face, mm

    # ---- beam bracket interface (outer faces of the legs) ----
    # (x, z) hole positions, mm: x measured along the face from the post-face
    # centreline, z measured from node bottom (z=0).
    bracket_bolt_pattern: tuple[tuple[float, float], ...] = (
        (-38.0, 60.0), (38.0, 60.0), (-38.0, 140.0), (38.0, 140.0),
    )
    bracket_bolt_diameter: float = 16.0  # mm

    # ---- pinwheel spine (owner architecture E: two halves bolted
    # back-to-back at a flat diagonal mating plane through the rod axis;
    # the pair completes the rod boss like a split bearing). Spine bolts
    # sit INSIDE the boss flat, in the strips beside the rod groove
    # (spot-faced seats) - flange ears were rejected by the undercut
    # screen (they overhang the legs and trap mold). ----
    spine_split: bool = False          # False = full-boss solo L (corner use)
    spine_bolt_diameter: float = 12.0
    spine_bolt_rows: int = 3

    # ---- assembly ----
    fit_clearance: float = 0.25       # node-to-post sliding fit, per side, mm
    base_plate_thickness: float = 0.0  # cast floor under the post footprint,
                                       # mm (0 = open boot; >0 = socket seat:
                                       # post end bears on the plate, forces
                                       # Architecture B storey-segment posts)

    # ---- casting / manufacturing ----
    fillet_radius: float = 8.0        # mm
    draft_angle: float = 2.0          # degrees, applied at pattern stage
    machining_allowance: float = 3.0  # mm on functional faces (pattern stage)
    shrink_factor: float = 0.02       # cast steel patternmaker's shrink

    # ---- materials ----
    casting_material: Material = field(default_factory=lambda: CAST_STEEL_A27_65_35)
    post_material: Material = field(default_factory=lambda: CFS_GRADE_50)
    bolt_material: Material = field(default_factory=lambda: BOLT_GRADE_8_8)

    def __post_init__(self) -> None:
        if self.post_thickness is None:
            self.post_thickness = gauge_to_design_thickness(self.post_gauge_mil)

        positive = [
            "post_size", "post_thickness", "engagement_length", "wall_thickness",
            "rib_thickness", "rib_depth", "corner_web_thickness", "bolt_diameter",
            "bolt_pitch", "bolt_gauge", "bolt_edge_distance", "rod_hole_diameter",
            "boss_diameter", "bracket_bolt_diameter", "fillet_radius",
        ]
        for name in positive:
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be > 0, got {getattr(self, name)}")
        for name in ("boss_height", "draft_angle", "machining_allowance",
                     "shrink_factor", "end_rib_thickness",
                     "base_plate_thickness"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be >= 0, got {getattr(self, name)}")
        if self.rib_count < 0:
            raise ValueError("rib_count must be >= 0")
        if self.spine_split:
            if self.spine_bolt_diameter <= 0:
                raise ValueError("spine_bolt_diameter must be > 0")
            if self.spine_bolt_rows < 2:
                raise ValueError("spine_bolt_rows must be >= 2 (joint moment)")
            strip = (self.boss_diameter - self.rod_hole_diameter) / 2
            if strip < 1.6 * self.spine_bolt_diameter:
                raise ValueError(
                    f"boss flat strip {strip:.1f} mm too narrow for spine "
                    f"bolts d={self.spine_bolt_diameter} (need >= "
                    f"{1.6 * self.spine_bolt_diameter:.1f}); enlarge "
                    "boss_diameter"
                )
        if self.bolt_rows < 1 or self.bolts_per_row < 1:
            raise ValueError("need at least one bolt row and one bolt per row")

        # rod hole must fit inside the boss with an annulus of casting wall
        if self.rod_hole_diameter + 2 * self.wall_thickness > self.boss_diameter:
            raise ValueError(
                "boss_diameter too small: rod hole + 2*wall_thickness "
                f"({self.rod_hole_diameter + 2 * self.wall_thickness:.1f}) > "
                f"boss_diameter ({self.boss_diameter:.1f})"
            )
        # bolt group must fit inside the engagement length
        group_z = (self.bolt_rows - 1) * self.bolt_pitch + 2 * self.bolt_edge_distance
        if group_z > self.engagement_length:
            raise ValueError(
                f"bolt group height {group_z:.1f} exceeds engagement_length "
                f"{self.engagement_length:.1f}"
            )
        # bolt row must fit across the post face
        group_w = (self.bolts_per_row - 1) * self.bolt_gauge + 2 * self.bolt_edge_distance
        if group_w > self.post_size:
            raise ValueError(
                f"bolt row width {group_w:.1f} exceeds post face {self.post_size:.1f}"
            )
        # bracket holes must land on the leg face
        half_face = self.post_size / 2
        for (x, z) in self.bracket_bolt_pattern:
            r = self.bracket_bolt_diameter / 2
            if not (-half_face + r < x < half_face + self.wall_thickness - r):
                raise ValueError(f"bracket hole x={x} off the leg face")
            if not (r < z < self.engagement_length - r):
                raise ValueError(f"bracket hole z={z} outside engagement length")

    # ---- derived geometry ----
    @property
    def rod_center_offset(self) -> float:
        """Diagonal setback c of the boss/rod centre beyond the post corner.

        Chosen so the boss cylinder is tangent to the post corner point:
        c*sqrt(2) = boss_radius, i.e. the boss just kisses the post corner
        and never intrudes into the post envelope.
        """
        return (self.boss_diameter / 2) / math.sqrt(2)

    @property
    def rod_center_xy(self) -> tuple[float, float]:
        h = self.post_size / 2 + self.rod_center_offset
        return (h, h)

    @property
    def total_height(self) -> float:
        """Overall height including top and bottom boss collars."""
        return self.engagement_length + 2 * self.boss_height

    def as_flat_dict(self) -> dict:
        """Flatten for the results contract (one Parquet row per candidate)."""
        d = asdict(self)
        for key in ("casting_material", "post_material", "bolt_material"):
            mat = d.pop(key)
            d[f"{key}_name"] = mat["name"]
            d[f"{key}_Fy"] = mat["Fy"]
            d[f"{key}_Fu"] = mat["Fu"]
        d["bracket_bolt_pattern"] = repr(self.bracket_bolt_pattern)
        return d


# ---------------------------------------------------------------------------
# Load cases
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class LoadCase:
    """One load case applied to the node+post+bolts assembly.

    Sign convention: axial_N positive = compression down the post (-Z on the
    node top interface). rod_tension_N positive = uplift tension in the tie
    rod (+Z on the rod bearing surface). shear_*_N positive along +X / +Y,
    applied at the bracket bolt pattern on the corresponding leg face.
    All magnitudes are UNFACTORED unless stated otherwise by the caller;
    factoring policy is decided at the check level, not here.
    """

    case_id: str
    description: str
    axial_N: float = 0.0
    rod_tension_N: float = 0.0
    shear_x_N: float = 0.0
    shear_y_N: float = 0.0

    def as_flat_dict(self) -> dict:
        return {
            "load_case": self.case_id,
            "lc_axial_N": self.axial_N,
            "lc_rod_tension_N": self.rod_tension_N,
            "lc_shear_x_N": self.shear_x_N,
            "lc_shear_y_N": self.shear_y_N,
        }


# ---------------------------------------------------------------------------
# NORM-BASED DESIGN INPUTS (owner delegated: "research what the norm is in
# LGSF and do it"). Sources in README "Adopted norms". These are defensible
# defaults for a panelized post-and-beam LGSF kit with open floor plans -
# NOT site-specific engineering. The sealing engineer must confirm every one.
# ---------------------------------------------------------------------------
FLOOR_LIVE_KPA = 1.92     # 40 psf residential live load (IRC / ASCE 7)
FLOOR_DEAD_KPA = 1.20     # ~25 psf: LGSF floor system + partitions/services
FACADE_DEAD_KPA = 0.75    # panelized facade self weight per face area
WIND_PRESSURE_KPA = 1.00  # PLACEHOLDER - genuinely site-specific (ASCE 7)
BAY_M = 6.0               # grid, set by practical CFS floor span (~6 m)
STOREY_M = 3.0            # storey height
N_STOREYS_DEFAULT = 5     # owner: "at least 5 storeys"

# LRFD adopted as the pipeline's design method (modern engineered-design
# norm under AISI S100; owner may flip to ASD - checks keep capacities
# nominal either way). Load factors: ASCE 7 strength combinations.
DESIGN_METHOD = "LRFD"
LF_DEAD, LF_LIVE, LF_WIND, LF_DEAD_COUNTER = 1.2, 1.6, 1.0, 0.9


def default_load_cases(n_storeys: int = N_STOREYS_DEFAULT) -> list[LoadCase]:
    """The five contract load cases at NORM-DERIVED magnitudes (LRFD level).

    Tributary model (documented so the sealing engineer can strike any line):
      * Building-corner node of a BAY_M x BAY_M grid; tributary floor area
        A = (BAY_M/2)^2 per storey.
      * One-way joists: one beam at the corner carries the floor strip
        (w = qu * BAY_M/2, end reaction w*L/2 = qu*A), the orthogonal beam
        carries facade only. The kit is symmetric, so BOTH brackets are
        designed for the floor-beam reaction (LC3 and LC4 individually);
        the combined case LC5 pairs one floor beam with one facade beam,
        which is the realizable simultaneous condition.
      * Gravity: qu = 1.2*D + 1.6*L per storey; axial accumulates linearly
        over n_storeys.
      * Uplift LC2: wind overturning of one braced corner line -
        storey force = WIND_PRESSURE_KPA * BAY_M * STOREY_M at each level,
        overturning moment about the base over lever arm BAY_M, minus the
        0.9*D counterweight tributary to the corner. WIND_PRESSURE_KPA is a
        PLACEHOLDER (site-specific); everything downstream of it is too.

    LOAD PATH ASSUMPTION (unchanged, engineer to confirm): accumulated
    compression crosses each storey joint by post continuity or direct
    bearing (architecture A/B, see README), accumulated uplift by the rod;
    the post-wall bolt group sees only own-storey beam reactions.
    """
    if n_storeys < 1:
        raise ValueError("n_storeys must be >= 1")
    n = float(n_storeys)
    A_trib = (BAY_M / 2.0) ** 2                              # m^2
    qu = LF_DEAD * FLOOR_DEAD_KPA + LF_LIVE * FLOOR_LIVE_KPA  # kPa (kN/m^2)
    per_storey_N = qu * A_trib * 1e3                          # N
    floor_beam_reaction_N = per_storey_N                      # = w*L/2, see docstring
    facade_beam_reaction_N = (LF_DEAD * FACADE_DEAD_KPA
                              * STOREY_M * (BAY_M / 2.0) * 1e3)

    storey_shear_N = WIND_PRESSURE_KPA * BAY_M * STOREY_M * 1e3
    m_ot_Nm = sum(storey_shear_N * STOREY_M * i for i in range(1, n_storeys + 1))
    counterweight_N = LF_DEAD_COUNTER * FLOOR_DEAD_KPA * A_trib * 1e3 * n
    uplift_N = max(0.0, LF_WIND * m_ot_Nm / BAY_M - counterweight_N)

    return [
        LoadCase("LC1", f"axial compression (gravity, {n_storeys} storeys, LRFD)",
                 axial_N=n * per_storey_N),
        LoadCase("LC2", f"rod uplift (wind overturning, {n_storeys} storeys, "
                        "0.9D counterweight, PLACEHOLDER wind)",
                 rod_tension_N=uplift_N),
        LoadCase("LC3", "bracket shear X (floor-beam end reaction, own storey)",
                 shear_x_N=floor_beam_reaction_N),
        LoadCase("LC4", "bracket shear Y (floor-beam end reaction, own storey)",
                 shear_y_N=floor_beam_reaction_N),
        LoadCase("LC5", "combined: stacked gravity + floor beam X + facade beam Y",
                 axial_N=n * per_storey_N,
                 shear_x_N=floor_beam_reaction_N,
                 shear_y_N=facade_beam_reaction_N),
    ]
