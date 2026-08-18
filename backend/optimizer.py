"""
Coal Rake Diversion Optimizer.

Uses Google OR-Tools (SCIP) to redistribute coal rakes across
Plant x Coal Company pairs, minimizing total weighted variable cost,
subject to:

  1. Company conservation      - each coal company supplies the same
                                  total rakes across all plants, before
                                  and after optimization.
  2. Plant total constraint    - each plant's total allocation stays
                                  within [plantMinPct, plantMaxPct] of
                                  its original current total.
  3. Plant-company constraint  - each individual plant-company
                                  allocation stays within the supplied
                                  [minRakes, maxRakes] bounds.
  4. Total rakes conservation  - implied automatically by (1): if every
                                  company's total is conserved, the
                                  grand total is conserved too.
  5. Integer allocation        - all decision variables are integers.
  6. RSD threshold constraint  - for each plant with a configured RSD/VC
                                  threshold, the plant's optimized
                                  rake-weighted VC must stay <= threshold
                                  (hard constraint; plants with a null/
                                  empty threshold are unconstrained).

current_vc is a manually entered, monthly input (see schemas.SourceInput) -
it is used as-is as the per-source cost coefficient. The optimizer never
generates, derives, or overwrites it; only the rake allocation changes.

No results are ever hardcoded - every number returned comes directly
from the OR-Tools solver or a direct rake-weighted-average of the
caller-supplied current_vc values.
"""

import math
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from ortools.linear_solver import pywraplp

from schemas import (
    OptimizeRequest,
    OptimizeResponse,
    AllocationResult,
    PlantVCResult,
    ConstraintStatusEntry,
    ValidationErrorEntry,
)

Row = Tuple[str, str, float, float, float, float]  # plant, company, current_rakes, current_vc, minR, maxR


def _weighted_vc(rakes_and_vc: List[Tuple[float, float]]) -> Optional[float]:
    """[(rakes, vc), ...] -> rake-weighted average VC, or None if total rakes is 0."""
    total_rakes = sum(r for r, _ in rakes_and_vc)
    if total_rakes <= 0:
        return None
    total_cost = sum(r * vc for r, vc in rakes_and_vc)
    return total_cost / total_rakes


def _validate(request: OptimizeRequest) -> List[ValidationErrorEntry]:
    """Business-rule validation beyond what the Pydantic field types already
    enforce (current_vc > 0, current_rakes >= 0 are checked at parse time).
    Catches things field constraints can't express, e.g. non-integer rakes."""
    errors: List[ValidationErrorEntry] = []
    for plant in request.plants:
        for src in plant.sources:
            if not math.isfinite(src.current_vc) or src.current_vc <= 0:
                errors.append(ValidationErrorEntry(
                    plant=plant.plant, company=src.company, field="current_vc",
                    message="Current VC is required for optimization.",
                ))
            if not math.isfinite(src.current_rakes) or src.current_rakes < 0:
                errors.append(ValidationErrorEntry(
                    plant=plant.plant, company=src.company, field="current_rakes",
                    message="Current rakes must be a non-negative number.",
                ))
            elif abs(src.current_rakes - round(src.current_rakes)) > 1e-6:
                errors.append(ValidationErrorEntry(
                    plant=plant.plant, company=src.company, field="current_rakes",
                    message="Current rakes must be a whole number.",
                ))
            if src.maxRakes < src.minRakes:
                errors.append(ValidationErrorEntry(
                    plant=plant.plant, company=src.company, field="maxRakes",
                    message="maxRakes must be >= minRakes.",
                ))
    return errors


def optimize(request: OptimizeRequest) -> OptimizeResponse:
    validation_errors = _validate(request)
    if validation_errors:
        return OptimizeResponse(status="validation_error", errors=validation_errors)

    # ---- Flatten input into a single list of (plant, company, current_rakes, current_vc, minR, maxR)
    rows: List[Row] = []
    for plant in request.plants:
        for src in plant.sources:
            rows.append((
                plant.plant, src.company, src.current_rakes,
                src.current_vc, src.minRakes, src.maxRakes,
            ))

    if not rows:
        return OptimizeResponse(status="Infeasible", message="No plant/source data supplied")

    weighted_vc_before = _weighted_vc([(r[2], r[3]) for r in rows])

    # ---- Company totals (must be conserved exactly)
    company_totals: Dict[str, float] = defaultdict(float)
    for plant, company, current_rakes, vc, minR, maxR in rows:
        company_totals[company] += current_rakes

    # ---- Plant totals (for the plantMinPct-plantMaxPct bound, and for
    # plant-level "before" VC / "not zero" guards)
    plant_totals: Dict[str, float] = defaultdict(float)
    for plant, company, current_rakes, vc, minR, maxR in rows:
        plant_totals[plant] += current_rakes

    by_plant: Dict[str, List[int]] = defaultdict(list)
    for idx, (plant, company, current_rakes, vc, minR, maxR) in enumerate(rows):
        by_plant[plant].append(idx)

    by_company: Dict[str, List[int]] = defaultdict(list)
    for idx, (plant, company, current_rakes, vc, minR, maxR) in enumerate(rows):
        by_company[company].append(idx)

    # Extract RSD/VC thresholds (None/empty = no RSD constraint)
    rsd_thresholds = {p.plant: p.rsd_threshold_vc for p in request.plants if p.rsd_threshold_vc is not None}

    # Helper function to build base solver and add core constraints
    def build_solver_with_core_constraints() -> Tuple["pywraplp.Solver", Dict[int, "pywraplp.Variable"]]:
        solver = pywraplp.Solver.CreateSolver("SCIP")
        if solver is None:
            return None, {}

        variables = {}
        for idx, (plant, company, current_rakes, vc, minR, maxR) in enumerate(rows):
            lb = int(round(minR))
            ub = int(round(maxR))
            if ub < lb:
                ub = lb  # degenerate but keep solver well-defined
            var = solver.IntVar(lb, ub, f"x_{idx}_{plant}_{company}")
            variables[idx] = var

        # Constraint 1: company conservation
        for company, idxs in by_company.items():
            solver.Add(
                solver.Sum(variables[i] for i in idxs) == int(round(company_totals[company]))
            )

        # Constraint 2: plant total within [plantMinPct, plantMaxPct]
        for plant, idxs in by_plant.items():
            current_total = plant_totals[plant]
            lower = int(round(current_total * request.plantMinPct))
            upper = int(round(current_total * request.plantMaxPct))
            plant_sum = solver.Sum(variables[i] for i in idxs)
            solver.Add(plant_sum >= lower)
            solver.Add(plant_sum <= upper)

        # Constraint 6: RSD threshold (hard). For each plant with a
        # configured threshold, the optimized rake-weighted VC must satisfy
        #   sum(x_i * vc_i) / sum(x_i) <= threshold
        # Linearized (multiplying through by the plant total, which is >= 0;
        # at zero rakes the constraint is trivially satisfied) as:
        #   sum(x_i * (vc_i - threshold)) <= 0
        for plant_name, threshold in rsd_thresholds.items():
            idxs = by_plant.get(plant_name)
            if idxs:
                solver.Add(
                    solver.Sum(variables[i] * (rows[i][3] - threshold) for i in idxs) <= 0
                )

        return solver, variables

    # ---- Single solve: minimize total weighted VC subject to the core
    # constraints and the (hard) RSD threshold constraints. No shutdown
    # minimization: thresholds are hard constraints, so an allocation that
    # cannot keep every thresholded plant at or below its threshold is
    # infeasible as a whole.
    solver, variables = build_solver_with_core_constraints()
    if solver is None:
        return OptimizeResponse(status="Infeasible", message="Could not create SCIP solver")

    # Objective: minimize total weighted variable cost
    solver.Minimize(
        solver.Sum(variables[idx] * rows[idx][3] for idx in range(len(rows)))
    )

    status = solver.Solve()

    infeasible_message = (
        "No feasible allocation satisfies company conservation, "
        "plant-total bounds, and plant-company bounds"
    )
    if rsd_thresholds:
        infeasible_message += ", and the configured RSD/VC thresholds"
    infeasible_message += " simultaneously."

    if status not in (pywraplp.Solver.OPTIMAL, pywraplp.Solver.FEASIBLE):
        return OptimizeResponse(
            status="Infeasible",
            weighted_vc_before=round(weighted_vc_before, 6) if weighted_vc_before is not None else None,
            message=infeasible_message,
        )

    solved_status = "Optimal" if status == pywraplp.Solver.OPTIMAL else "Feasible"

    optimized_rakes_by_idx = {idx: variables[idx].solution_value() for idx in range(len(rows))}
    weighted_vc_after = _weighted_vc([(optimized_rakes_by_idx[i], rows[i][3]) for i in range(len(rows))])

    allocations = []
    for idx, (plant, company, current_rakes, vc, minR, maxR) in enumerate(rows):
        opt_val = optimized_rakes_by_idx[idx]
        allocations.append(AllocationResult(
            plant=plant, company=company,
            current_rakes=current_rakes, optimized_rakes=opt_val,
            source_vc=vc, delta_rakes=opt_val - current_rakes,
            minRakes=minR, maxRakes=maxR,
        ))

    # ---- Plant-level results
    plants_result: List[PlantVCResult] = []
    for plant, idxs in by_plant.items():
        cur_total = sum(rows[i][2] for i in idxs)
        opt_total = sum(optimized_rakes_by_idx[i] for i in idxs)
        cur_vc = _weighted_vc([(rows[i][2], rows[i][3]) for i in idxs])
        opt_vc = _weighted_vc([(optimized_rakes_by_idx[i], rows[i][3]) for i in idxs])
        delta_vc = (opt_vc - cur_vc) if (cur_vc is not None and opt_vc is not None) else None

        threshold = rsd_thresholds.get(plant)
        if threshold is not None and opt_vc is not None:
            exceeded = opt_vc > threshold + 1e-6
            margin = opt_vc - threshold
        else:
            exceeded = False
            margin = None

        plants_result.append(PlantVCResult(
            plant=plant,
            rsd_threshold_vc=threshold,
            current_rakes=cur_total, optimized_rakes=opt_total,
            current_vc=round(cur_vc, 6) if cur_vc is not None else None,
            optimized_vc=round(opt_vc, 6) if opt_vc is not None else None,
            delta_vc=round(delta_vc, 6) if delta_vc is not None else None,
            exceeded_threshold=exceeded,
            threshold_margin=round(margin, 6) if margin is not None else None,
        ))

    # Calculate actual shutdowns in the returned solution
    total_shutdowns = sum(1 for p in plants_result if p.exceeded_threshold)

    # ---- Constraint validation report (post-hoc check of the solved values)
    constraint_status = []

    for company, idxs in by_company.items():
        total_after = sum(optimized_rakes_by_idx[i] for i in idxs)
        ok = abs(total_after - company_totals[company]) < 0.5
        constraint_status.append(ConstraintStatusEntry(
            name=f"Company conservation: {company}",
            satisfied=ok,
            detail=f"{company_totals[company]:.0f} -> {total_after:.0f}",
        ))

    for plant, idxs in by_plant.items():
        current_total = plant_totals[plant]
        total_after = sum(optimized_rakes_by_idx[i] for i in idxs)
        lower = current_total * request.plantMinPct
        upper = current_total * request.plantMaxPct
        ok = (total_after >= lower - 0.5) and (total_after <= upper + 0.5)
        constraint_status.append(ConstraintStatusEntry(
            name=f"Plant total bound: {plant}",
            satisfied=ok,
            detail=f"{total_after:.0f} within [{lower:.0f}, {upper:.0f}]",
        ))

    for plant_name, threshold in rsd_thresholds.items():
        idxs = by_plant.get(plant_name)
        if not idxs:
            continue
        plant_vc = _weighted_vc([(optimized_rakes_by_idx[i], rows[i][3]) for i in idxs])
        if plant_vc is None:
            ok = True
            detail = "no rakes allocated (constraint trivially satisfied)"
        else:
            ok = plant_vc <= threshold + 1e-6
            detail = f"{plant_vc:.4f} <= {threshold:.4f}"
        constraint_status.append(ConstraintStatusEntry(
            name=f"RSD threshold: {plant_name}",
            satisfied=ok,
            detail=detail,
        ))

    total_before = sum(r[2] for r in rows)
    total_after_all = sum(a.optimized_rakes for a in allocations)
    constraint_status.append(ConstraintStatusEntry(
        name="Global total rakes conserved",
        satisfied=abs(total_before - total_after_all) < 0.5,
        detail=f"{total_before:.0f} -> {total_after_all:.0f}",
    ))

    vc_improvement = None
    if weighted_vc_before is not None and weighted_vc_after is not None:
        vc_improvement = weighted_vc_before - weighted_vc_after

    return OptimizeResponse(
        status=solved_status,
        weighted_vc_before=round(weighted_vc_before, 6) if weighted_vc_before is not None else None,
        weighted_vc_after=round(weighted_vc_after, 6) if weighted_vc_after is not None else None,
        vc_improvement=round(vc_improvement, 6) if vc_improvement is not None else None,
        plants=plants_result,
        allocations=allocations,
        constraint_status=constraint_status,
        total_shutdowns=total_shutdowns,
    )
