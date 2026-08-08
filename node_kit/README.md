# node_kit — cast steel corner node, parametric design-space exploration

Parametric pipeline for an L-shaped **cast steel corner node** in a light-gauge
steel framing (LGSF) kit system. Four nodes + posts + beams form one structural
bay; bays repeat to form a building. The node wraps two adjacent faces of a
cold-formed steel (CFS) box post, bolts through the post wall, receives beam
brackets on its outer faces, and carries a vertical through-hole for a
continuous tie rod (uplift/tension through stacked storeys).

**Design philosophy:** the thin CFS post wall governs capacity, not the
casting. The casting is deliberately over-strong; the optimisation objective is
minimum casting mass subject to the CFS connection checks passing. The casting
is never analysed in isolation — every FEA model is casting + post wall + bolts
with contact.

This design will be reviewed and sealed by a licensed engineer. Everything here
is written for that audience: clause references, explicit assumptions, stated
units.

## Units

`mm`, `N`, `MPa` (N/mm²) everywhere. Density in kg/mm³, reported mass in kg,
angles in degrees. No imperial units anywhere in code; CFS gauge designations
(33/43/54/68/97 mil) are mapped to metric design thickness in
`params.GAUGE_TABLE_MM` and used only as labels.

## Stack (all open source, all headless)

| Role         | Tool                                   |
|--------------|----------------------------------------|
| Geometry     | cadquery (OCCT) → STEP + STL           |
| Meshing      | gmsh (Python API), meshio              |
| Solver       | CalculiX `ccx` (Abaqus-style `.inp`)   |
| Optimisation | pymoo (NSGA-II)                        |
| Data         | pandas + pyarrow (Parquet)             |
| Tests        | pytest                                 |

Install:

```bash
pip install cadquery gmsh meshio pymoo pandas pyarrow numpy scipy pytest matplotlib
apt-get install calculix-ccx        # or conda-forge: calculix
pip install -e node_kit
```

Solver installed in this environment: **CalculiX 2.21** (Debian
`calculix-ccx`), confirmed via `ccx -v`.

Run:

```bash
cd node_kit
pytest                          # all tests incl. validation gates
python -m node_kit.cli export   # default node STEP + STL into runs/
```

## ⚠️ Placeholders awaiting real values — DO NOT DESIGN AGAINST THESE

The project owner has **not yet supplied** the following. Current values are
order-of-magnitude stand-ins so the pipeline could be built and validated.
Any optimisation output produced with them is meaningless for design.

| Item | Placeholder in `params.py` | Needed from owner |
|------|---------------------------|-------------------|
| LC1 gravity compression | 50 kN | real factored/unfactored magnitude + which |
| LC2 rod uplift          | 40 kN | " |
| LC3 bracket shear X     | 20 kN | " |
| LC4 bracket shear Y     | 20 kN | " |
| Post outer size         | 150 mm square | roll-former's actual section |
| Post gauge              | 97 mil | actual gauge |
| CFS coil grade          | 50 ksi (Fy 345 / Fu 450 MPa) | actual coil spec |
| Bolt grade              | ISO 8.8 | actual fastener spec |

Also unstated: whether load magnitudes are ASD or LRFD level. The check layer
(Milestone 4) keeps capacities unfactored and applies the resistance/safety
factor at the demand/capacity step, so this must be resolved before results
are read.

## Geometry decisions requiring engineer review

1. **Bracket bolt holes pass straight through the leg wall.** In the real
   joint they either continue through the CFS post wall behind (shared
   clamping) or must become tapped blind holes. Modelled as through-holes.
2. **Tie-rod boss tangent to the post corner:** rod centreline sits
   `boss_radius/√2` diagonally beyond the post corner point, so the boss never
   intrudes into the post envelope. Check rod washer/nut clearance to the post
   corner at the collar faces.
3. **Ribs are horizontal flange plates** on the leg outer faces (top/bottom
   edges first, then evenly spaced), with a matching collar ring around the
   boss. Beam brackets seat between rib bands; the layout validator rejects
   bracket holes that pierce a rib band.
4. **As-machined model has no draft**; draft, shrink and machining allowance
   are applied at pattern stage (`pattern.py`, Milestone 8). Top/bottom convex
   edges are left sharp in the as-machined model.
5. **Post modelled with sharp corners**; real CFS box sections have corner
   radii ≈ 2t. The pocket corner of the casting is kept sharp (no fillet) so
   it cannot clash; bearing at the corner is therefore slightly conservative
   in FEA.

## Multi-storey load path (added after owner confirmed >= 5 storeys)

ASSUMPTION (critical, engineer to confirm): storeys stack
node-bearing-on-node through the machined top/bottom faces, with the tie rod
continuous through the rod hole. Consequences, encoded in
`params.default_load_cases(n_storeys=...)`:

* Accumulated gravity compression passes casting-to-casting and NEVER
  through the thin CFS post wall. Accumulated uplift passes through the rod.
* The post-wall bolt group sees only the node's own-storey beam reactions
  (LC3/LC4 magnitudes do not scale with storey count).
* Storey accumulation is modelled linearly as a placeholder; real
  accumulation needs the building's load takedown from the owner.
* "Oversizing the casting" therefore does NOT raise joint capacity - the
  post wall and bolt group set it. The levers that do: post gauge, bolt
  count/diameter/pattern, rod diameter, engagement length, bearing areas.

## FEA simplifications log

(Extended at each milestone; every entry needs a justification.)

| # | Simplification | Justification |
|---|----------------|---------------|
| 1 | (Milestone 6) Bolts as beam/spring connectors + node-to-post surface contact, no thread/preload modelling | Bearing/tilting in 1–2.6 mm sheet governs; AISI checks carry the code capacity, FEA carries load distribution + stiffness |
| 2 | (Milestone 6) Post modelled as shell at design thickness | Captures wall bearing/tilting flexibility that a rigid post would hide |
| 3 | Linear elastic material for screening runs | Optimisation ranks candidates; final candidate gets a review pass by the sealing engineer |
| 4 | Gate 1: tip load applied as equal nodal split; fully clamped root face | Total force exact; Saint-Venant artifacts local; at L/h = 20 shear (+~0.2 %) and clamp restraint (−~0.2 %) are inside the 2 % gate |
| 5 | Gate 2: quarter-symmetric finite plate, d/W = 0.05 | Howland finite-width correction ~1 %, inside the 5 % gate |
| 6 | Gate 3: rigid pin, all rotations fixed (no tilting), frictionless linear-penalty contact, bilinear hardening (Fy→Fu at 10 % strain) | Compares against washered/double-shear m_f values where tilting is suppressed; acceptance is a bounded band (code value below FEA ultimate, FEA below 1.15× the most generous code configuration) because no exact closed form exists — band FLAGGED for engineer review |

## Validation gates (Milestone 3) — results

| Gate | Benchmark | Acceptance | Result |
|------|-----------|------------|--------|
| 1 | Cantilever tip deflection vs PL³/3EI | < 2 % | **0.20 %** ✓ |
| 2 | Plate with hole, Kt = 3.0 | < 5 % | **0.34 %** (Kt 3.010) ✓ |
| 3 | Single-bolt lap shear vs AISI J3.3.1/J3.3.2 | banded (see log #6) | **PASS** — FEA plateau 56.2 kN = 1.009 × J3.3.1(mf=1.33) 55.7 kN; J3.3.2 nominal 27.9 kN = 0.50 × FEA ultimate (code conservative, as calibrated) ✓ |

Gate 3 detail (97 mil sheet, M12 pin, Fu = 450 MPa placeholder): bearing
force 47.5 kN at 1.0 mm pin travel, 53.7 kN at 1.5 mm, plateau 56.2 kN.
Full history in runs/validation/gate3/result.json (regenerate with pytest).

## UNVERIFIED items for engineer review

- `params.GAUGE_TABLE_MM`: SSMA-standard design thicknesses; the 95%
  delivered-thickness rule is quoted from AISI S100 but the clause number is
  not yet verified against the printed standard (Milestone 4 will resolve).
- `checks/aisi_s100.py` (partial, Milestone 3 slice):
  - Table J3.3.1-1 bearing factor C breakpoints (3.0 / 4−0.1·d/t / 1.8)
    quoted from memory — confirm against the printed table.
  - Table J3.3.1-2 m_f rows for no-washer and oversized-hole cases (0.75,
    1.07) — UNVERIFIED; washered values (1.00, 1.33) high confidence but
    still to be checked.
  - Eq. J3.3.2-1 SI conversion coefficient α = 0.0394 — confirm.
  - φ/Ω values quoted in docstrings (0.60/2.50 for J3.3.1, 0.65/2.22 for
    J3.3.2) — UNVERIFIED, not yet used in any calculation.
  - J3.3.2 applicability limits (thickness range, hole type, spacing/edge
    minimums) not yet enforced — Milestone 4.
- Gate 3 acceptance band (FEA ≥ J3.3.2 nominal, ≤ 1.15 × J3.3.1 mf=1.33) is
  engineering judgment, not a code provision — review.

## Open system-level questions for the owner

1. **Bracing scheme**: lateral storey shear must resolve through a bracing
   system (strap X-bracing, K-braces, or portal action). The node currently
   has no brace attachment feature. If straps/struts terminate at the node,
   say so and a parametric brace lug (plate tab or bolt boss on the outer
   corner) gets added to geometry.py.
2. **Bracket detail**: do bracket bolts pass through the post wall (shared
   clamping) or thread into the casting?
3. **Node-on-node stacking**: confirm the storey-stacking bearing assumption
   in "Multi-storey load path" above.

## Repo layout

```
node_kit/
  src/node_kit/
    params.py        # NodeParams + LoadCase + materials, single source of truth
    geometry.py      # as-machined solid, STEP/STL export        [Milestone 2 ✓]
    mesh.py          # STEP -> gmsh -> .inp                      [Milestone 3]
    fea.py           # ccx driver + .frd/.dat parsing            [Milestone 3]
    checks/
      aisi_s100.py   # AISI S100 connection checks               [Milestone 4]
      castability.py # manufacturability checks                  [Milestone 5]
    objective.py     # candidate -> metrics row                  [Milestone 6]
    search.py        # NSGA-II driver                            [Milestone 7]
    report.py        # Pareto + recommendation                   [Milestone 7]
    pattern.py       # foundry pattern + print split             [Milestone 8]
  tests/             # pytest; validation gates must pass first
  validation/        # closed-form benchmark cases               [Milestone 3]
  runs/              # results.parquet + artifacts (gitignored)
```
