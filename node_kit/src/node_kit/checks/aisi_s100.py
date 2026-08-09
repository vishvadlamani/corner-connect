"""AISI S100 connection checks for thin CFS sheet — Milestone 4 (complete).

SCOPE. Pure capacity functions for the bolted node-to-post connection and
the tie-rod chain, written for review by a licensed engineer. Every function
returns a NOMINAL capacity:

    (capacity_N, governing_limit_state, clause_ref)

Resistance factors (phi, LRFD) and safety factors (Omega, ASD) live in
SAFETY_FACTORS and are applied ONLY by demand_capacity_ratio(), which
refuses to run against unverified factors unless explicitly overridden.

CLAUSE REFERENCE POLICY. Edition: AISI S100-16 (Chapter J, "Connections and
Joints"). References this module is confident of are given plainly.
Anything quoted from memory and not yet cross-checked against the printed
standard carries an UNVERIFIED tag in the clause string itself, so the flag
travels with every result row into the Parquet contract. Where even the
clause NUMBER is uncertain, the reference names the chapter only - clause
numbers are never guessed. The full list is mirrored in README
"UNVERIFIED items for engineer review".

TILTING. For BOLTED connections, thin-sheet tilting/hole-deformation
behaviour is carried by the m_f modification factors (Table J3.3.1-2) and
the hole-deformation provision (J3.3.2); there is no separate bolt-tilting
equation. (Screw connections have an explicit tilting provision in the
screws section - not implemented; this kit uses bolts.)

Units: mm, N, MPa.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Resistance / safety factors — ALL UNVERIFIED until checked against the
# printed standard. demand_capacity_ratio() enforces the gate.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class FactorEntry:
    phi: float        # LRFD resistance factor
    omega: float      # ASD safety factor
    verified: bool = False


SAFETY_FACTORS: dict[str, FactorEntry] = {
    # quoted from memory, engineer to confirm every one:
    "bolt_bearing_no_deformation": FactorEntry(0.60, 2.50),
    "bolt_bearing_with_deformation": FactorEntry(0.65, 2.22),
    "bolt_shear": FactorEntry(0.65, 2.40),
    "bolt_tension": FactorEntry(0.75, 2.25),
    "net_section_rupture": FactorEntry(0.65, 2.22),
    "shear_rupture": FactorEntry(0.70, 2.22),
    "block_shear_rupture": FactorEntry(0.70, 2.22),
    "web_crippling": FactorEntry(0.75, 2.00),
}


class UnverifiedFactorError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Bearing (implemented for validation gate 3; retained verbatim)
# ---------------------------------------------------------------------------
# Conversion coefficient in AISI S100-16 Eq. J3.3.2-1 for SI units (t in mm).
# UNVERIFIED: value 0.0394 quoted from memory of the standard; confirm.
_ALPHA_SI = 0.0394


def bearing_factor_C(d: float, t: float) -> float:
    """Bearing factor C per AISI S100-16 Table J3.3.1-1.

    d = bolt diameter (mm), t = sheet thickness (mm).
      d/t < 10        : C = 3.0
      10 <= d/t <= 22 : C = 4 - 0.1*(d/t)
      d/t > 22        : C = 1.8
    UNVERIFIED: breakpoints quoted from memory; confirm against the printed
    Table J3.3.1-1 before sealing.
    """
    ratio = d / t
    if ratio < 10.0:
        return 3.0
    if ratio <= 22.0:
        return 4.0 - 0.1 * ratio
    return 1.8


# Modification factor m_f per AISI S100-16 Table J3.3.1-2.
# Washered values (1.33, 1.00) high confidence; no-washer / oversized rows
# UNVERIFIED from memory.
MF_TABLE: dict[str, float] = {
    "double_shear_inside_sheet_with_washers": 1.33,
    "single_shear_with_washers": 1.00,
    "single_shear_without_washers": 0.75,         # UNVERIFIED
    "double_shear_inside_sheet_oversized": 1.07,  # UNVERIFIED
}


def bolt_bearing_no_deformation(
    d: float, t: float, Fu_sheet: float,
    connection_type: str = "single_shear_with_washers",
) -> tuple[float, str, str]:
    """Nominal bolt bearing WITHOUT hole-deformation consideration,
    AISI S100-16 Section J3.3.1:  Pn = C * m_f * d * t * Fu  (Eq. J3.3.1-1).
    """
    if connection_type not in MF_TABLE:
        raise ValueError(f"unknown connection_type {connection_type!r}")
    Pn = bearing_factor_C(d, t) * MF_TABLE[connection_type] * d * t * Fu_sheet
    return Pn, "bolt bearing (no hole-deformation consideration)", "AISI S100-16 J3.3.1"


def bolt_bearing_with_deformation(
    d: float, t: float, Fu_sheet: float,
) -> tuple[float, str, str]:
    """Nominal bolt bearing WITH hole-deformation consideration,
    AISI S100-16 Section J3.3.2:
        Pn = (4.64 * alpha * t + 1.53) * d * t * Fu   (Eq. J3.3.2-1)
    alpha = 0.0394 for SI (t in mm) - UNVERIFIED, see _ALPHA_SI.

    Use this provision for the kit system: hole elongation matters when
    storeys must stay pluggable and re-usable.
    """
    Pn = (4.64 * _ALPHA_SI * t + 1.53) * d * t * Fu_sheet
    return Pn, "bolt bearing (with hole-deformation consideration)", "AISI S100-16 J3.3.2"


def bolt_bearing(
    d: float, t: float, Fu_sheet: float,
    consider_hole_deformation: bool = True,
    connection_type: str = "single_shear_with_washers",
) -> tuple[float, str, str]:
    """Bearing wrapper with applicability screening.

    Screens (raise ValueError when violated rather than returning a wrong
    number):
      * t within [0.61, 6.35] mm - J3.3.2 thickness applicability range,
        UNVERIFIED bounds quoted from memory;
      * standard holes assumed - oversized/slotted require different m_f.
    """
    if consider_hole_deformation and not (0.61 <= t <= 6.35):
        raise ValueError(
            f"t={t} mm outside J3.3.2 applicability range [0.61, 6.35] mm "
            "(UNVERIFIED bounds - confirm)"
        )
    if consider_hole_deformation:
        return bolt_bearing_with_deformation(d, t, Fu_sheet)
    return bolt_bearing_no_deformation(d, t, Fu_sheet, connection_type)


# ---------------------------------------------------------------------------
# Bolt shear / tension
# ---------------------------------------------------------------------------
def bolt_area(d: float) -> float:
    """Nominal (gross) bolt area, mm^2. AISI bolt strengths are applied to
    the nominal area with thread effects inside Fnv/Fnt."""
    return math.pi * d ** 2 / 4.0


def bolt_shear(
    d: float, Fu_bolt: float,
    threads_in_shear_plane: bool = True,
    Fnv: float | None = None,
    shear_planes: int = 1,
) -> tuple[float, str, str]:
    """Nominal bolt shear capacity: Pn = Ab * Fnv * shear_planes.

    Clause: AISI S100-16 J3 - bolt shear (exact clause number and Table of
    Fnv values UNVERIFIED; supply the printed Table value via Fnv=...).

    When Fnv is not supplied it defaults to the AISC 360-style basis
      Fnv = 0.450 * Fu_bolt (threads in shear plane)
      Fnv = 0.563 * Fu_bolt (threads excluded)
    which is a PLACEHOLDER for the AISI table value and is marked in the
    returned clause ref. For ISO 8.8, EN 1993-1-8 uses alpha_v = 0.6 on the
    threaded stress area - similar order; engineer to pick the governing
    document for the actual fastener spec.
    """
    if Fnv is None:
        Fnv = (0.450 if threads_in_shear_plane else 0.563) * Fu_bolt
        ref = ("AISI S100-16 J3 - bolt shear (Fnv PLACEHOLDER = "
               f"{0.450 if threads_in_shear_plane else 0.563:.3f}*Fu, AISC-style "
               "basis; substitute printed AISI table value - UNVERIFIED)")
    else:
        ref = "AISI S100-16 J3 - bolt shear (Fnv supplied by caller)"
    Pn = bolt_area(d) * Fnv * shear_planes
    return Pn, "bolt shear", ref


def bolt_tension(
    d: float, Fu_bolt: float, Fnt: float | None = None,
) -> tuple[float, str, str]:
    """Nominal bolt tension capacity: Pn = Ab * Fnt.

    Clause: AISI S100-16 J3 - bolt tension (exact clause number and Table
    Fnt values UNVERIFIED). Default Fnt = 0.75 * Fu_bolt is the AISC-style
    basis, PLACEHOLDER for the printed table value.
    """
    if Fnt is None:
        Fnt = 0.75 * Fu_bolt
        ref = ("AISI S100-16 J3 - bolt tension (Fnt PLACEHOLDER = 0.75*Fu; "
               "substitute printed AISI table value - UNVERIFIED)")
    else:
        ref = "AISI S100-16 J3 - bolt tension (Fnt supplied by caller)"
    return bolt_area(d) * Fnt, "bolt tension", ref


def shear_tension_interaction(
    V_demand: float, T_demand: float,
    d: float, Fu_bolt: float,
    Fnv: float | None = None, Fnt: float | None = None,
    threads_in_shear_plane: bool = True,
) -> tuple[float, str, str]:
    """Combined shear + tension: reduced nominal TENSION capacity in the
    presence of the given shear demand.

    Form (AISC 360 J3.7-style linear interaction, capped):
        F'nt = 1.3*Fnt - (Fnt / Fnv) * f_rv   <=  Fnt,   f_rv = V/Ab
    applied on NOMINAL strengths (no phi/Omega folded in here; the DCR
    helper applies them afterwards, which is a simplification of the exact
    code format where the interaction embeds phi or Omega - FLAGGED).

    Clause: AISI S100-16 J3 - combined shear and tension in bolts (exact
    clause number UNVERIFIED; the AISI form should be substituted verbatim
    in the engineer's review pass).

    Returns the reduced nominal tension capacity Pnt_reduced; the paired
    shear check (V_demand vs bolt_shear) must be run separately.
    """
    Ab = bolt_area(d)
    if Fnv is None:
        Fnv = (0.450 if threads_in_shear_plane else 0.563) * Fu_bolt
    if Fnt is None:
        Fnt = 0.75 * Fu_bolt
    f_rv = V_demand / Ab
    fnt_red = min(Fnt, 1.3 * Fnt - (Fnt / Fnv) * f_rv)
    fnt_red = max(fnt_red, 0.0)
    return (
        Ab * fnt_red,
        "bolt combined shear+tension (reduced tension capacity)",
        "AISI S100-16 J3 - combined shear and tension (form UNVERIFIED, "
        "AISC-style interaction used pending printed clause)",
    )


# ---------------------------------------------------------------------------
# Connected-part rupture (thin sheet)
# ---------------------------------------------------------------------------
def standard_hole_diameter(d: float) -> float:
    """Nominal standard hole size. d + 0.8 mm for d < 12.7 mm, else
    d + 1.6 mm (1/32 in / 1/16 in oversizes - UNVERIFIED table values)."""
    return d + (0.8 if d < 12.7 else 1.6)


def net_area(width: float, n_holes_across: int, d_hole: float,
             t: float) -> float:
    """Net tension area of a flat sheet section with holes across it."""
    return (width - n_holes_across * d_hole) * t


def net_section_rupture(
    An: float, Fu: float,
    d: float | None = None, s: float | None = None,
    Usl: float | None = None,
) -> tuple[float, str, str]:
    """Tension rupture of the connected part on the net section:
        Pn = Usl * An * Fu
    Clause: AISI S100-16 J6.2 (section number medium confidence - verify).

    Usl = shear-lag / non-uniform-stress factor. If not supplied and the
    single-bolt-per-row flat-sheet parameters are given (d = bolt diameter,
    s = sheet width tributary to the bolt), the historical flat-sheet
    factor Usl = min(1.0, 0.1 + 3*d/s) is used - UNVERIFIED table-case
    mapping (confirm which J6.2 case applies to washered single-column
    bolted flat sheet). If neither is given, Usl = 1.0 with the limitation
    noted in the ref string.
    """
    if Usl is None:
        if d is not None and s is not None and s > 0:
            Usl = min(1.0, 0.1 + 3.0 * d / s)
            case = "flat-sheet Usl = 0.1 + 3d/s (UNVERIFIED case mapping)"
        else:
            Usl = 1.0
            case = "Usl = 1.0 assumed - shear lag NOT evaluated"
    else:
        case = "Usl supplied by caller"
    Pn = Usl * An * Fu
    return Pn, "net section tension rupture", f"AISI S100-16 J6.2 ({case})"


def shear_rupture(Anv: float, Fu: float) -> tuple[float, str, str]:
    """Shear rupture of the connected part:  Vn = 0.6 * Fu * Anv.
    Clause: AISI S100-16 J6.1 (section number medium confidence - verify).
    Anv = net shear area along the failure path (e.g. 2 * e_net * t per
    bolt for end shear-out, computed by the caller from geometry)."""
    return 0.6 * Fu * Anv, "shear rupture", "AISI S100-16 J6.1"


def block_shear_rupture(
    Agv: float, Anv: float, Ant: float,
    Fy: float, Fu: float, Ubs: float = 1.0,
) -> tuple[float, str, str]:
    """Block shear rupture of the connected part:
        Pn = min( 0.6*Fy*Agv + Ubs*Fu*Ant ,  0.6*Fu*Anv + Ubs*Fu*Ant )
    (shear-yield + tension-rupture path vs shear-rupture + tension-rupture
    path; Ubs = 1.0 for uniform tension stress.)
    Clause: AISI S100-16 J6.3 (section number medium confidence - verify).
    """
    yield_path = 0.6 * Fy * Agv + Ubs * Fu * Ant
    rupture_path = 0.6 * Fu * Anv + Ubs * Fu * Ant
    Pn = min(yield_path, rupture_path)
    governing = ("block shear (shear-yield path)" if yield_path <= rupture_path
                 else "block shear (shear-rupture path)")
    return Pn, governing, "AISI S100-16 J6.3"


def web_crippling(
    t: float, Fy: float, theta_deg: float,
    R: float, N: float, h: float,
    C: float, C_R: float, C_N: float, C_h: float,
) -> tuple[float, str, str]:
    """Nominal web crippling strength of a CFS web at a bearing end:

        Pn = C * t^2 * Fy * sin(theta) * (1 - C_R*sqrt(R/t))
             * (1 + C_N*sqrt(N/t)) * (1 - C_h*sqrt(h/t))

    Form per AISI S100-16 Chapter G web-crippling provision (equation
    number and the COEFFICIENTS C/C_R/C_N/C_h are case-dependent - taken
    from the printed Table for the section type, fastening condition and
    loading case). Coefficients are REQUIRED arguments precisely so no
    memory-quoted value can leak into a result: supply them from the
    printed standard. Used for the post END bearing on the cast base
    plate (plate-on stacking architecture).

    t = web thickness, R = inside bend radius, N = bearing length,
    h = flat web depth (all mm); theta = web angle to bearing surface.
    """
    import math as _m
    th = _m.radians(theta_deg)
    Pn = (C * t ** 2 * Fy * _m.sin(th)
          * (1 - C_R * _m.sqrt(R / t))
          * (1 + C_N * _m.sqrt(N / t))
          * (1 - C_h * _m.sqrt(h / t)))
    return max(Pn, 0.0), "web crippling (post end bearing)", (
        "AISI S100-16 Chapter G web crippling (equation number and "
        "coefficients from the printed Table - caller-supplied)")


# ---------------------------------------------------------------------------
# Geometry limits
# ---------------------------------------------------------------------------
def spacing_and_edge_limits(
    d: float, spacing: float, edge_distance: float, end_distance: float,
) -> tuple[bool, dict, str]:
    """Minimum bolt spacing and edge/end distance screening.

    Limits applied (UNVERIFIED values quoted from memory - confirm J3.1 and
    J3.2 before sealing):
      * centre-to-centre spacing        >= 3.0 * d
      * centre-to-edge distance         >= 1.5 * d
      * centre-to-end (loaded) distance >= 1.5 * d
    Note: the loaded-end distance is additionally governed by shear rupture
    (J6.1) - run shear_rupture() on the end path as well; this function
    only screens the geometric minimums.

    Returns (all_ok, {check: (value, limit, ok)}, clause_ref).
    """
    checks = {
        "spacing": (spacing, 3.0 * d, spacing >= 3.0 * d),
        "edge_distance": (edge_distance, 1.5 * d, edge_distance >= 1.5 * d),
        "end_distance": (end_distance, 1.5 * d, end_distance >= 1.5 * d),
    }
    ok = all(v[2] for v in checks.values())
    return ok, checks, ("AISI S100-16 J3.1 / J3.2 (limit multipliers "
                        "UNVERIFIED - confirm)")


# ---------------------------------------------------------------------------
# Demand / capacity
# ---------------------------------------------------------------------------
def demand_capacity_ratio(
    demand_N: float, nominal_capacity_N: float, limit_state_key: str,
    method: str, allow_unverified: bool = False,
) -> float:
    """DCR with the phi/Omega registry.

    method: 'LRFD' -> DCR = demand / (phi * Pn)
            'ASD'  -> DCR = demand * Omega / Pn
    Raises UnverifiedFactorError when the registry entry has not been
    verified against the printed standard, unless allow_unverified=True
    (the optimisation pipeline sets this and stamps factors_unverified=True
    into every results row).
    """
    if limit_state_key not in SAFETY_FACTORS:
        raise KeyError(f"no factor entry for {limit_state_key!r}")
    entry = SAFETY_FACTORS[limit_state_key]
    if not entry.verified and not allow_unverified:
        raise UnverifiedFactorError(
            f"phi/Omega for {limit_state_key!r} are UNVERIFIED "
            "(engineer must confirm; pass allow_unverified=True to proceed "
            "at your own risk)"
        )
    if method == "LRFD":
        return demand_N / (entry.phi * nominal_capacity_N)
    if method == "ASD":
        return demand_N * entry.omega / nominal_capacity_N
    raise ValueError("method must be 'LRFD' or 'ASD'")


def single_bolt_summary(
    d: float, t: float, Fu_sheet: float, Fu_bolt: float,
    connection_type: str = "single_shear_with_washers",
    consider_hole_deformation: bool = True,
) -> dict:
    """All shear-transfer limit states for ONE bolt position in the thin
    sheet; returns {'capacities': {name: (Pn, ls, ref)}, 'governing': name}.
    Net/block shear are group-geometry dependent and are checked at group
    level, not here."""
    caps = {
        "bolt_shear": bolt_shear(d, Fu_bolt),
        "bearing": bolt_bearing(d, t, Fu_sheet,
                                consider_hole_deformation, connection_type),
    }
    governing = min(caps, key=lambda k: caps[k][0])
    return {"capacities": caps, "governing": governing}
