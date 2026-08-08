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

## System classification (owner decision, 2026-08)

**Panelized post-and-beam kit** — the frame (nodes + posts + beams) is the
complete structural system to give open floor plans; wall/floor panels are
NON-loadbearing infill. NOT volumetric/modular. Kit drawing rule that
follows: infill panels connect with deflection-head / slotted details so
frame movement never loads them (load follows stiffness, not intention).

## Adopted norms (owner delegated: "research the norm in LGSF and do it")

Encoded in params.py; every value is a defensible LGSF-practice default,
NOT site-specific engineering — the sealing engineer confirms or replaces:

| Quantity | Value | Basis |
|---|---|---|
| Floor live load | 1.92 kPa (40 psf) | IRC/ASCE 7 residential |
| Floor dead load | 1.20 kPa (~25 psf) | LGSF floor + partitions/services |
| Facade dead | 0.75 kPa | panelized facade allowance |
| Wind pressure | 1.00 kPa | **PLACEHOLDER — site-specific (ASCE 7)** |
| Grid / bay | 6.0 m | practical CFS floor span limit (open plans) |
| Storey height | 3.0 m | LGSF practice |
| Storeys | 5 | owner: "at least 5" |
| Design method | LRFD, ASCE 7 factors | modern engineered CFS design |
| Post | 150 mm sq, 97 mil | built-up CFS box practice; Chapter E member check pending |

Resulting LC magnitudes at the ground-storey corner node (LRFD level):
LC1 = 203 kN, LC2 = 86 kN, LC3 = LC4 = 40.6 kN, LC5 combined (see
`params.default_load_cases()` for the tributary derivation, written out).

## ⚠️ Remaining placeholders — confirm before sealing

Loads are now NORM-DERIVED (see "Adopted norms"; LRFD adopted), which
upgrades them from fiction to defensible defaults — but they are still not
site- or product-specific. Outstanding:

| Item | Current value | Needed from owner |
|------|---------------|-------------------|
| Wind pressure | 1.0 kPa | site wind per ASCE 7 (drives LC2) |
| Post outer size / gauge | 150 mm sq / 97 mil (norm-based) | roll-former confirmation |
| CFS coil grade | 50 ksi (Fy 345 / Fu 450 MPa) | actual coil spec |
| Bolt grade | ISO 8.8, M12 | actual fastener spec + printed AISI Fnv/Fnt |
| Joint architecture | undecided | clamp (A) vs stack (B) — see open questions |

## Castability (Milestone 5) — first real design change from the pipeline

The undercut screen (voxel-column parity along the declared pull axis)
proved geometry v1 UNMOLDABLE on the declared diagonal parting: the boss
bulged past each leg's outer face, trapping 14.9 % of mold columns in
reentrant pockets (same at the rib collar ring). Geometry v2 adds
45-degree TANGENT WEBS blending boss->legs and ring->ribs; the screen now
passes (and, as a discriminating control, correctly REJECTS a vertical
pull, where the rib flanges act as a spool). Knock-on changes: boss
Ø60->Ø52, bracket holes ±45->±38 mm (faces must stay flat up to the web
line - enforced in validate_hole_layout), default mass 10.28 -> 9.56 kg.

Checks: min wall, adjacent-section ratio, draft (param, verified on the
pattern at M8), fillet presence+radius, undercut/core screen, hot-spot
inscribed-sphere estimate (resolution-limited, tet-mesh based). Limits in
params.CastabilityLimits are common steel-sand-casting practice —
**confirm with the actual foundry**.

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
| 7 | Assembly (M6): post shell UNPERFORATED at design thickness | Post-side hole bearing capacity is carried by the AISI checks; the shell's job is global wall bending/tilting flexibility |
| 8 | Assembly: bolts = rigid casting-rim + distributing coupling on the shell + 3 SPRING2 (axial EA/L, shear 12EI/L³) | Load-distribution fidelity, not local stress; bolt forces recovered exactly as k·Δ; rigid rim slightly stiffens the casting hole locally |
| 9 | Assembly: frictionless contact with fit_clearance = 0.25 mm/side sliding gap | Matches a real plug-together kit fit; contact engages only where prying closes the gap; also removes zero-gap chatter (5× solver speedup) |
| 10 | Assembly: linear kinematics, contact iterations only | Screening-level; final candidate gets a nonlinear review pass |
| 11 | LC1 applied as collar-annulus bearing (Architecture B stack path) | Worst case for the casting; under Architecture A it vanishes into post continuity |
| 12 | LC3/LC4 magnitudes applied as VERTICAL (−z) forces at the face-A/face-B bracket rims | They are floor-beam END REACTIONS (gravity shear); the load-case "x/y" labels the beam's span direction, not the force direction |
| 13 | Casting/post utilization = peak nodal von Mises vs Fy | Peak sits at coupling/re-entrant regions and is mesh-sensitive; conservative screening indicator, engineer reviews the field, not just the peak |

## Candidate evaluation (Milestone 6)

```bash
python -m node_kit.cli evaluate            # default candidate, 5 storeys
python -m node_kit.cli evaluate --coarse   # fast screening meshes
```

One Parquet row per (candidate, load case) in `runs/results.parquet`,
carrying the full contract: every NodeParams field, load case id and
magnitudes, casting mass, casting/post peak von Mises, joint stiffness,
every AISI ratio by name, every castability check (pass/value/limit),
per-LC and candidate pass flags, governing limit state, per-bolt forces
(JSON), solve wall-clock, git SHA, timestamp, RNG seed, design method and
the factors_unverified stamp. The five load cases run as parallel ccx
processes (wall clock ~ the slowest case).

Mass anatomy of the default node (why a casting outweighs folded sheet):
the two leg plates are 6.1 kg of the 9.5 kg total (64 %); the boss adds
~2.9 kg net. The same legs in 4 mm folded sheet would be 2.0 kg — the
casting pays a min-wall tax (~8 mm castable floor vs 4–5 mm sheet) and
buys back the integrated rod boss, no welds, and machined datums. An
aggressive-but-castable parameter set (8 mm wall, 160 mm engagement,
slimmer boss/ribs) builds at 5.2 kg — the optimiser's hunting ground.

## Milestone 6 results — default candidate, 5 storeys, norm loads (LRFD)

| LC | casting peak vM | post wall peak | stiffness | governing | pass |
|----|------------------|----------------|-----------|-----------|------|
| LC1 stack 203 kN | 228 MPa (0.95×Fy) | 43 MPa | 2.1e6 N/mm | casting vM | ✗ |
| LC2 uplift 86 kN | 189 MPa (0.79×Fy) | 90 MPa | 1.9e6 N/mm | casting vM | ✗ |
| LC3 beam 40.6 kN | 72 MPa (0.30×Fy) | 86 MPa | 7.4e5 N/mm | bearing 0.64 | ✗* |
| LC4 beam 40.6 kN | 69 MPa (0.29×Fy) | 87 MPa | 7.4e5 N/mm | bearing 0.64 | ✗* |
| LC5 combined | 349 MPa (**1.45×Fy**) | 71 MPa | 2.0e6 N/mm | casting vM | ✗ |

\* LC3/LC4 fail only via the candidate-level castability hot-spot flag
(corner inscribed sphere 27.8 mm vs 24 limit); their strength ratios pass.

READING (the design story so far): the legs and bolted connection behave
exactly per the design philosophy — casting loafing at ~0.3, post-wall
bearing governing at 0.64. The FAILURE is the Architecture-B stacking
path: 203 kN accumulated gravity squeezed through the small corner collar
annulus drives the boss/collar junction to and past yield (LC1 0.95,
LC5 1.45 — peak values at a sharp junction, mesh-sensitive, but the trend
is real). Three exits, all live options:
  1. **Architecture A (clamp)** — LC1/LC5 stacking vanishes into post
     continuity; the collar only ever sees LC2 uplift (0.79, passes).
  2. **Cast floor plate** (`base_plate_thickness > 0`, IMPLEMENTED) —
     stacked gravity bears post-end-on-plate over the full footprint
     instead of the corner collar. +2.1 kg (11.65 vs 9.53), moldable on
     the same diagonal parting (0 trapped columns). Adds an AISI web-
     crippling check for the post end ring (Milestone 4 backlog) and
     forces storey-segment posts.
  3. **Bigger collar/boss** — parameter-space fix, fights the bracket-face
     clearance; the optimiser can explore it.

## Joint topology trade study (owner exploring, 2026-08)

| | 2-face open L (current) | open + cast floor (implemented) | 4-face closed sleeve (proposed) |
|---|---|---|---|
| Post faces engaged | 2 | 2 | 4 (bolt capacity ~2× per row) |
| Fastening | bolts exit bare post faces (blind side) | same | **through-bolts casting-to-casting across the box** — no blind fastening, bearing on TWO walls |
| Gravity path | bolts (clamp) or collar (stack) | post end on plate (best bearing) | sleeve + optional internal shelf |
| Casting | 2-part mold, no cores (verified) | 2-part mold, no cores (verified) | REQUIRES an internal core + deep internal machining or as-cast socket fit |
| Post continuity | compatible (clamp) | forces storey segments | slide-through compatible unless shelved |
| Kit rationalisation | corner only; edge/interior joints need T and + variants | same | **one universal hub serves corner/edge/interior** (brackets on any of 4 faces) |
| Torsion/symmetry | asymmetric | asymmetric | symmetric, torsionally stiff |
| Erection | clamp-on anywhere | drop-in, self-seating | drop-in, self-jigging |

The closed sleeve does NOT compromise strength — it is the strongest and
most product-rational option; its costs are foundry (cored pattern, core
shift tolerance, internal access for machining) and unit mass (~4 walls
vs 2). Decision pending owner; geometry.py currently implements the two
open topologies via `base_plate_thickness`.

### D — TWIN-L SPLIT COLLAR (owner idea, 2026-08): two of the SAME L
casting, the second rotated 180° about the post axis, clamping all four
post faces. Analysis:

* The current L already pairs correctly: a 180° rotation maps it onto the
  opposite two faces; the symmetric hole pattern (±gauge, ±bracket-x)
  means every post bolt lines up with the opposite half — **every bolt
  becomes a casting-to-casting through-bolt** bearing on BOTH post walls
  (≈2× bolted capacity per row, and the pair confines the thin walls).
* Legs meet only at the two free corners, end-face to side-face, with no
  overlap (verify with a small leg-end setback for field tolerance —
  parametric TODO).
* One pattern, one SKU: use ONE half at light corners, TWO wherever more
  capacity or beam faces are needed (corner/edge/interior joints from the
  same casting). Two tie-rod corners per paired joint.
* No core (each half is the proven 2-part moldable L), clamps a
  CONTINUOUS post (Architecture A - the LC5 stacking failure never
  happens), install/retrofit at any height.
* Caveats: paired halves connect only through bolts and post (less
  torsional stiffness than a monolithic sleeve - corner interlock feature
  possible later); long through-bolts across the hollow box may need
  compression sleeves at torque-up; per-joint mass when paired is 2× one
  half (optimiser drives the half down).

RECOMMENDATION (pipeline author): adopt D as the product architecture —
single-L clamp (A) and twin-L collar are the same casting, so Milestone 7
optimises ONE part that serves both; keep the floor plate (B) as a
base/transfer-level variant; drop the cored monolithic sleeve (C), which
the twin-L dominates on castability at equal function. Engineering next
steps for D: leg-end setback param, hole-symmetry guarantee as a
validation rule, paired-assembly FEA variant, AISI bearing treated as
double-shear-inside-sheet configuration (m_f 1.33 rows to verify).

## Milestone 7 — NSGA-II search results (2026-08, seed 42)

`python -m node_kit.search --pop 12 --gen 7` (84 evaluations, 74 unique
candidates, 43 feasible, 31 castability failures; clamp-architecture load
set LC2 + both-beams envelope; COARSE screening meshes).

**Recommended: 7.45 kg (−22 % vs the 9.53 kg default), governing
utilisation 0.70, all checks pass.** Parameters: wall 14.3, engagement
148.4, rib 9.2×8.1, corner web 9.2, boss Ø52.3. Full reasoning in
runs/report.md; front in runs/pareto.png.

Findings the engineer should read:
1. **The Pareto front is flat at ~0.70** across 7.4–11 kg: post-wall
   bolt bearing (J3.3.2) is the capacity floor no matter how much casting
   is added — the quantitative proof of the design philosophy. Capacity
   moves only via bolt count/diameter/gauge (fixed in this search) or the
   twin-L pairing (~2× bearing).
2. **The optimiser thickened the wall (12→14.3) partly to satisfy the
   hot-spot check**, whose limit scales with nominal wall (2.0×wall).
   Legitimate under the stated limit, but FLAGGED: the foundry should
   confirm whether the ~27 mm corner section needs the extra wall or a
   riser note instead - a relative limit can be gamed.
3. Engagement dropped 200→148 (bolt group + brackets still fit) - the
   biggest single mass saver.
4. Mass floor at ~7.4 kg comes from castability + layout-validity fences,
   not strength.

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
- `checks/aisi_s100.py` (Milestone 4 complete — every flag below also
  travels inside the returned clause_ref strings, so it lands in every
  results row):
  - Table J3.3.1-1 bearing factor C breakpoints (3.0 / 4−0.1·d/t / 1.8)
    quoted from memory — confirm against the printed table.
  - Table J3.3.1-2 m_f rows for no-washer and oversized-hole cases (0.75,
    1.07) — UNVERIFIED; washered values (1.00, 1.33) high confidence but
    still to be checked.
  - Eq. J3.3.2-1 SI conversion coefficient α = 0.0394 — confirm.
  - J3.3.2 thickness applicability range [0.61, 6.35] mm — UNVERIFIED
    bounds (enforced by the `bolt_bearing` wrapper, which raises rather
    than extrapolating).
  - Bolt Fnv/Fnt: placeholders 0.450·Fu / 0.563·Fu (shear, threads in/out)
    and 0.75·Fu (tension) on the AISC-360 basis — the printed AISI table
    values for the actual fastener spec MUST be substituted; exact clause
    numbers for bolt shear/tension not asserted.
  - Combined shear+tension: AISC-360-style linear interaction used as the
    form; the printed AISI clause must be substituted verbatim. Applied on
    nominal strengths with φ/Ω folded in afterwards — a simplification of
    the code format, FLAGGED.
  - Rupture section numbers J6.1 / J6.2 / J6.3 — medium confidence, verify.
  - Net-section shear-lag factor Usl = min(1, 0.1 + 3d/s) case mapping for
    washered single-column bolted flat sheet — UNVERIFIED.
  - Spacing/edge minimums (3d spacing, 1.5d edge/end) — UNVERIFIED
    multipliers.
  - Standard hole oversizes (+0.8 mm / +1.6 mm at 12.7 mm break) —
    UNVERIFIED.
  - ALL φ/Ω entries in `SAFETY_FACTORS` — UNVERIFIED;
    `demand_capacity_ratio` refuses to run without `allow_unverified=True`,
    and the pipeline stamps `factors_unverified=True` into every row it
    produces that way.
- Worked-example tests (tests/test_aisi_s100.py tier 2) are SKIPPED
  placeholders: supply, from the AISI Cold-Formed Steel Design Manual, the
  printed values for (1) a single-bolt bearing example, (2) a bolted
  flat-sheet net-section example, (3) a block-shear example, and (4) the
  bolt Fnv/Fnt table entries for the actual fastener spec.
- Gate 3 acceptance band (FEA ≥ J3.3.2 nominal, ≤ 1.15 × J3.3.1 mf=1.33) is
  engineering judgment, not a code provision — review.
- ASD vs LRFD is still undeclared by the owner; no DCR is reportable until
  it is.

## Open system-level questions for the owner

1. **Bracing scheme**: lateral storey shear must resolve through a bracing
   system (strap X-bracing, K-braces, or portal action). The node currently
   has no brace attachment feature. If straps/struts terminate at the node,
   say so and a parametric brace lug (plate tab or bolt boss on the outer
   corner) gets added to geometry.py.
2. **Bracket detail**: do bracket bolts pass through the post wall (shared
   clamping) or thread into the casting?
3. **Joint architecture — the single highest-leverage open decision.**
   The post column carries the accumulated gravity along its length in ANY
   architecture; the question is only how that force crosses each storey
   joint plane. Two coherent options, both keeping accumulated gravity out
   of the bolt group (precedent: AISC bearing-type column splices, ISO
   container corner castings, modular corner-fitting systems):

   * **A — "clamp" (competitor-style)**: post continuous over multiple
     storeys, nodes clamp onto it at floor levels, tie rods run OUTSIDE the
     post. Accumulated gravity crosses the joint by post continuity - no
     transfer at all. Bolts carry only per-storey beam reactions. Current
     node geometry (open sleeve) already suits this. Splices, where the
     post length runs out, must be bearing-type.
   * **B — "stack"**: storey-segment posts; accumulated gravity crosses the
     joint by DIRECT BEARING (post end -> node seat -> node/post below).
     Requires a bearing seat the current geometry does NOT have (internal
     ledge or cap diaphragm - parametric feature to add, cored pattern).
     Better per-storey demountability; machined bearing surfaces.

   Anti-pattern to avoid (pallet-racking style): per-storey gravity via
   tabs/fasteners bearing in thin walls is workable (racking does it) but
   costs joint looseness and mandatory stiffness testing (EN 15512); for a
   stacked building system the accumulated load must never do this.

4. **Post member check is outside this pipeline's scope**: the ground-storey
   post carries n_storeys x per-storey gravity as a COLUMN (AISI Chapter E
   member design: local buckling of the thin wall governs well below gross
   yield). This pipeline checks the CONNECTION; the post member check must
   be run at building level and may set the real height ceiling.

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
