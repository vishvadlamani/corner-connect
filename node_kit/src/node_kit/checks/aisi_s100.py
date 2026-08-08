"""AISI S100 connection checks for thin CFS sheet.

STATUS: PARTIAL - Milestone 3 needed the bolt bearing provisions for
validation gate 3, so those are implemented now. The remaining checks
(bolt shear, net-section rupture, block shear, spacing/edge limits,
shear+tension interaction, DCR helper) land in Milestone 4 and currently
raise NotImplementedError individually.

Edition: clause references are to AISI S100-16 (Chapter J, "Connections
and Joints", J3 "Bolted Connections"). Anything not yet cross-checked
against the printed standard is marked UNVERIFIED in its docstring AND
listed in README "UNVERIFIED items". Do not remove a flag without checking
the paper standard.

Units: mm, N, MPa. All capacities returned are NOMINAL (unfactored);
resistance factors / safety factors are applied by the DCR helper
(Milestone 4) once the owner confirms ASD vs LRFD.

Every capacity function returns:
    (capacity_N: float, governing_limit_state: str, clause_ref: str)
"""

from __future__ import annotations

# Conversion coefficient in AISI S100-16 Eq. J3.3.2-1 for SI units (t in mm).
# UNVERIFIED: value 0.0394 quoted from memory of the standard; confirm.
_ALPHA_SI = 0.0394


def bearing_factor_C(d: float, t: float) -> float:
    """Bearing factor C per AISI S100-16 Table J3.3.1-1.

    d = bolt diameter (mm), t = sheet thickness (mm).
      d/t < 10        : C = 3.0
      10 <= d/t <= 22 : C = 4 - 0.1*(d/t)
      d/t > 22        : C = 1.8
    UNVERIFIED: table values quoted from memory; confirm against the
    printed Table J3.3.1-1 before sealing.
    """
    ratio = d / t
    if ratio < 10.0:
        return 3.0
    if ratio <= 22.0:
        return 4.0 - 0.1 * ratio
    return 1.8


# Modification factor m_f per AISI S100-16 Table J3.3.1-2.
# Keys are connection types. The washered values (1.33, 1.00) are high
# confidence; the no-washer / oversized-hole rows are UNVERIFIED from memory
# and must be confirmed before use in a sealed design.
MF_TABLE: dict[str, float] = {
    "double_shear_inside_sheet_with_washers": 1.33,
    "single_shear_with_washers": 1.00,
    "single_shear_without_washers": 0.75,       # UNVERIFIED
    "double_shear_inside_sheet_oversized": 1.07,  # UNVERIFIED
}


def bolt_bearing_no_deformation(
    d: float, t: float, Fu_sheet: float,
    connection_type: str = "single_shear_with_washers",
) -> tuple[float, str, str]:
    """Nominal bolt bearing strength WITHOUT consideration of bolt hole
    deformation, AISI S100-16 Section J3.3.1:

        Pn = C * m_f * d * t * Fu        (Eq. J3.3.1-1)

    Applies to the thin connected sheet. Returns nominal capacity in N.
    Resistance/safety factors (phi = 0.60 / Omega = 2.50 - UNVERIFIED,
    confirm in J3.3.1) are NOT applied here.
    """
    if connection_type not in MF_TABLE:
        raise ValueError(f"unknown connection_type {connection_type!r}")
    mf = MF_TABLE[connection_type]
    C = bearing_factor_C(d, t)
    Pn = C * mf * d * t * Fu_sheet
    return Pn, "bolt bearing (no hole-deformation consideration)", "AISI S100-16 J3.3.1"


def bolt_bearing_with_deformation(
    d: float, t: float, Fu_sheet: float,
) -> tuple[float, str, str]:
    """Nominal bolt bearing strength WITH consideration of bolt hole
    deformation, AISI S100-16 Section J3.3.2:

        Pn = (4.64 * alpha * t + 1.53) * d * t * Fu    (Eq. J3.3.2-1)

    alpha = 0.0394 for SI units with t in mm (UNVERIFIED, see _ALPHA_SI).
    This provision is calibrated to a serviceability hole-deformation limit
    and gives lower capacities than J3.3.1; use it when hole elongation
    matters (it does for a stacked kit system that must stay pluggable).

    Applicability limits (thickness range, standard holes, spacing/edge
    distance minimums) are enforced in Milestone 4's wrapper; UNVERIFIED
    until then. Resistance/safety factors (phi = 0.65 / Omega = 2.22 -
    UNVERIFIED) are NOT applied here.
    """
    Pn = (4.64 * _ALPHA_SI * t + 1.53) * d * t * Fu_sheet
    return Pn, "bolt bearing (with hole-deformation consideration)", "AISI S100-16 J3.3.2"


# ---------------------------------------------------------------------------
# Milestone 4 (not yet implemented)
# ---------------------------------------------------------------------------
def bolt_shear(*args, **kwargs):
    """Bolt shear capacity. Milestone 4."""
    raise NotImplementedError("Milestone 4")


def net_section_rupture(*args, **kwargs):
    """Tension rupture of the connected part on the net section. Milestone 4."""
    raise NotImplementedError("Milestone 4")


def block_shear_rupture(*args, **kwargs):
    """Block shear / shear rupture of the connected part. Milestone 4."""
    raise NotImplementedError("Milestone 4")


def spacing_and_edge_limits(*args, **kwargs):
    """Minimum bolt spacing and edge distance limits. Milestone 4."""
    raise NotImplementedError("Milestone 4")


def shear_tension_interaction(*args, **kwargs):
    """Combined shear + tension interaction. Milestone 4."""
    raise NotImplementedError("Milestone 4")


def demand_capacity_ratio(*args, **kwargs):
    """DCR helper applying phi/Omega once ASD vs LRFD is confirmed. Milestone 4."""
    raise NotImplementedError("Milestone 4")
