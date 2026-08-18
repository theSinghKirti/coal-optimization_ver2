# Optimizer Architecture — Baseline Audit

This document records the state of every optimization implementation in this
repository **before** Module 1 (backend-authority) changes were applied, and
defines which implementation is authoritative going forward.

There are **three independent optimizers**. They are not variations of the
same algorithm — they solve different mathematical problems, over different
scopes, with different constraints, and can legitimately disagree on the same
input data. Nothing here is a bug in any of them individually; the problem
this module fixes is that the UI did not make clear which one produces the
official result.

---

## 1. JavaScript client-side optimizer (`dashboard_template.html`)

**Functions:** `optimizePlant(plant)`, `applyOptimizedToPlant(plant)`,
`currentBlendedVC(plant)` (not an optimizer — a weighted-average display
helper).

**UI entry points (pre-Module-1 labels):**
- `#optimizeOneBtn` — "Optimize this plant"
- `#optimizeAllBtn` — "Optimize all plants"

**Algorithm:** Bounded fractional water-filling, in *share space* (0–1) per
plant. Sorts the plant's own sources cheapest-first by `currentVC` and fills
each up to its `maxShare`, in order, until 100% of that plant's rake volume
is allocated.

**Scope:** **One plant at a time, in isolation.** Each call only sees that
plant's own sources.

**Constraints applied:**
- Sum of a plant's source shares stays at 100% (that plant's total is
  conserved).
- Each source's share stays within `[minShare, maxShare]` (derived from the
  ± flex-band slider around its current share, or manually edited).

**Constraints NOT applied:**
- No company-wide conservation — nothing stops this optimizer from
  recommending more of a coal company's supply than that company actually
  has once every plant's demand on it is summed.
- No integer rounding — works in continuous share space; rakes are only
  rounded when `applyOptimizedToPlant()` converts the result back to
  absolute rakes.

**Side effects:** `applyOptimizedToPlant()` **overwrites `state`** —
specifically `s.currentRakes` for every source in the plant — so clicking
"Optimize this plant" / "Optimize all plants" changes what the dashboard
subsequently treats as the *current* allocation. It does not touch
`s.currentVC` (source-level VC is never modified by any optimizer, per the
existing input/result-state separation).

**Speed:** Instant, runs entirely in the browser, no network call.

---

## 2. Root CLI optimizer (`main.py`)

**Functions:** `optimize_plant(df, plant, ...)`, `optimize_all_plants(...)`.

**Entry point:** `python main.py` (STEP 6 of the script), writes results to
stdout and `output/output.xlsx`. Not connected to the dashboard or the
FastAPI backend in any way — it is a standalone script reading directly from
`input.xlsx`.

**Algorithm:** Tries to build and solve its **own, separate** OR-Tools model
(`pywraplp.Solver.CreateSolver("SCIP")` — a second, independent instantiation
unrelated to `backend/optimizer.py`) per plant. **If that fails for any
reason** (OR-Tools not installed, solver returns non-optimal, or any other
exception), it **silently falls back to a greedy heuristic**: sort sources
cheapest-first, fill each up to its upper bound until the plant's total is
used up.

**Scope:** One plant at a time (`optimize_all_plants` just loops this per
plant). Same as the JS optimizer in this respect.

**Constraints applied:**
- Plant total conserved at the current (or daily-override) total.
- Each source's rakes bounded to `[40%, 120%]` of its own base value
  (`SOURCE_MIN_PCT` / `SOURCE_MAX_PCT` constants), or pinned if frozen via
  `--freeze`.

**Constraints NOT applied:**
- No company-wide conservation (same gap as the JS optimizer).
- No plant-total 80–110% bound is enforced in the greedy fallback path
  (the OR-Tools attempt does conserve the plant total exactly via an
  equality constraint, but that's a different rule than the backend's
  80–110% *range*).

**Pre-Module-1 problem:** the printed report and `output/output.xlsx`'s
"Optimized Rakes" / "Target Status" columns present this result exactly the
same way whether OR-Tools succeeded or the greedy fallback silently kicked
in — there is no indication in the output of which path was used.

---

## 3. Backend OR-Tools optimizer (`backend/optimizer.py`)

**Function:** `optimize(request)`, exposed as `POST /optimize`
(`backend/main.py`).

**Algorithm:** A single, portfolio-wide Integer Linear Program, solved once
per request with Google OR-Tools (SCIP). One integer decision variable per
(plant, company) pair, across **every plant in the request simultaneously**.

**Scope:** The entire portfolio at once — this is the only implementation
that sees all plants and all companies together in one model.

**Constraints applied (all 6, exactly, nothing hardcoded):**
1. **Company conservation** — each coal company's total rakes across *all*
   plants combined must equal exactly its current total.
2. **Plant total bound** — each plant's total stays within
   `[plantMinPct, plantMaxPct]` (default 80–110%) of its current total.
3. **Plant-company bound** — each individual (plant, company) allocation
   stays within the caller-supplied `[minRakes, maxRakes]`.
4. **Global total conservation** — implied automatically by (1).
5. **Integer allocation** — all decision variables are `IntVar`.
6. **RSD shutdown minimization (lexicographic)** — a plant with a
   configured `rsd_threshold_vc` is *in RSD* when its optimized
   rake-weighted VC exceeds its threshold; plants without a threshold are
   never counted. Thresholds are soft, solved in two stages:
   - **Stage A (minimize RSD count):** a binary `shutdown[p]` per
     thresholded plant with a Big-M constraint
     `sum(x_i*(vc_i - threshold)) <= M_p * shutdown[p]`; minimize
     `sum(shutdown[p])` and record the minimum count `K`.
   - **Stage B (optimize cost):** re-solve the model to minimize the
     portfolio's total variable cost, subject to
     `sum(shutdown[p]) == K` to lock in the minimal RSD count (equivalent
     to `<= K`, since `K` is the minimum achievable sum).
   Each plant's response row reports `rsd_status` (`safe` | `rsd` |
   `no_constraint`), `exceeded_threshold`, `threshold_margin` and the
   configured `rsd_threshold_vc`; `total_shutdowns` reports the number of
   RSD plants in the final allocation.

**Failure behavior (already correct, pre-Module-1):** there is **no
fallback logic of any kind** in this file. If the solver can't create
(`CreateSolver` returns `None`) or can't find an optimal/feasible solution,
the function returns an explicit `status: "Infeasible"` (or a validation
error for malformed input) — it never silently substitutes a heuristic
result. This already satisfies "no silent fallback," which is why Module 1
required no changes to `backend/optimizer.py`'s solving logic itself.

**Speed:** One HTTP round-trip; typically ~1–2 seconds for this portfolio's
size, plus Render cold-start latency on the free tier if the container has
been idle.

---

## Side-by-side comparison

| | JS client-side | Root CLI (`main.py`) | Backend (`backend/optimizer.py`) |
|---|---|---|---|
| Scope | 1 plant | 1 plant | **All plants at once** |
| Company conservation | ❌ No | ❌ No | ✅ Yes (exact) |
| Plant total bound | 100% conserved | 100% conserved (OR-Tools path) | 80–110% range (configurable) |
| Source bound | ± flex band (%) | 40–120% of base | Caller-supplied min/max rakes |
| Integer rakes | ❌ Continuous shares | ✅ (OR-Tools path) / ✅ (greedy) | ✅ Always |
| Silent fallback on failure | N/A (always "succeeds" in share space, or reports infeasible min/max sums) | ⚠️ **Yes** — greedy heuristic, unlabeled (fixed in Module 1) | ✅ No — explicit `Infeasible`/error always |
| Network required | No | No | Yes |
| Overwrites live dashboard state | ✅ Yes, on click | N/A (separate process) | ❌ No — only renders into its own result panel |

## Why they can disagree on identical input

Because the JS and CLI optimizers never enforce company conservation, they
can recommend an allocation that looks better *for one plant in isolation*
but would be impossible in reality — it would require some coal company to
supply more total rakes than it actually has once every plant's request on
that company is added up. The backend's stricter, portfolio-wide model
routinely finds a *smaller* improvement than the client-side estimate for
exactly this reason — not because it's worse, but because it's the only one
checking a constraint that actually matters. See the KPI-vs-server-result
discrepancy this caused in dashboard testing (₹0.23 client-side estimate vs.
₹0.0548 real achievable improvement on the same data) — an early real-world
confirmation of this exact difference in scope.

## Authority decision for Module 1

**`backend/optimizer.py`, via `POST /optimize`, is the sole authoritative
optimizer.** The JS water-filling optimizer is retained as an explicitly
labeled non-authoritative **preview** (fast, in-browser, useful for a quick
"what if" without waiting on a network round-trip) — see the frontend
changes below. The CLI (`main.py`) is a separate standalone tool not wired
to the dashboard at all; it no longer silently substitutes greedy results
for OR-Tools results without saying so (see Module 1 changes to `main.py`).
