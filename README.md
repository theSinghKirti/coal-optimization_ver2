# UPRVUNL Coal Rake-Mix Optimizer

## Structure
```
coal-optimizer/
├── input.xlsx              # Source workbook (Dashboard / July 2026 (3) / Calculation Sheet)
├── daily_variation.csv      # Sample daily-availability overrides (--daily flag)
├── main.py                  # CLI: reads input.xlsx, reproduces Excel VC, optimizes,
│                             #      regenerates the dashboard HTML from live data
├── dashboard_template.html  # The dashboard SOURCE (edit this, not output/index.html)
├── output/
│   ├── index.html           # Generated: dashboard_template.html + live data from input.xlsx
│   └── output.xlsx          # Generated: main.py's CLI run output
└── backend/
    ├── main.py              # FastAPI app -> POST /optimize
    ├── optimizer.py          # Google OR-Tools (SCIP) solver - the real optimizer
    ├── schemas.py            # Pydantic request/response models
    └── requirements.txt
```

## Running the CLI (Python side)

```bash
pip install pandas openpyxl ortools
python main.py                                  # basic run
python main.py --daily daily_variation.csv      # apply daily availability overrides
python main.py --freeze "Parichha:NCL=52"       # lock a plant-source allocation
python main.py --dashboard dashboard_template.html   # regenerate output/index.html
                                                       # with fresh data from input.xlsx
```

## Running the optimizer backend (FastAPI + OR-Tools)

```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```
Health check: `GET http://localhost:8000/health`
Optimize:     `POST http://localhost:8000/optimize`

## Opening the dashboard

Open `output/index.html` directly in a browser. It's fully self-contained
(logo and data are embedded as base64/inline JSON - no external files needed
except the Google Fonts CDN link, which degrades gracefully if offline).

The "Optimize Allocation (OR-Tools)" button expects the backend above running
at `http://localhost:8000`. Override the URL by setting
`window.OPTIMIZER_API_BASE` before the dashboard's script runs, or editing the
`OPTIMIZER_API_BASE` constant near the bottom of `dashboard_template.html`.

## Official Optimization Flow

This project has **three separate optimization implementations** (see
`docs/OPTIMIZER_ARCHITECTURE.md` for the full comparison), but only **one is
official**: `backend/optimizer.py`, via `POST /optimize`. Everything else in
the dashboard is a fast, non-authoritative preview.

1. The dashboard collects plant/source input (Current Rakes, Current VC,
   Min %/Max % bounds) for every plant.
2. Clicking **"Optimize Allocation (OR-Tools)"** sends the *entire
   portfolio* - every plant, every source - to `POST /optimize` in one
   request.
3. `backend/optimizer.py` builds a single Integer Linear Program across all
   plants at once and solves it with Google OR-Tools (SCIP), enforcing:
   - exact company-wide rake conservation across all plants,
   - each plant's total within `[plantMinPct, plantMaxPct]` (default
     80-110%) of its current total,
   - each plant-company allocation within the supplied min/max rakes,
   - integer-only rake counts.
4. The server's response is authoritative and is rendered into the
   **"Official Optimization Result"** panel. This is the only place in the
   dashboard where a genuinely final, constraint-checked allocation appears.
5. The **"Preview Plant Mix"** / **"Preview All Plant Mixes"** buttons (and
   the "Portfolio avg VC · preview" KPI card) run a different, much simpler
   algorithm entirely in the browser - a per-plant water-filling estimate
   with no cross-plant company conservation. It's useful for an instant
   what-if before committing to a real solve, but it is explicitly labeled
   "preview" everywhere it appears and must never be read as the final
   answer.

A few things worth being explicit about, since they're easy to misread:

- **Current VC is an input, not an output.** You type it in per source; no
  optimizer here ever generates or overwrites it. Only the *rake
  allocation* is optimized - the cost coefficients stay fixed.
- **Company-wise conservation is enforced only in the backend.** Neither
  the dashboard's client-side preview nor the root CLI (`main.py`) checks
  it - both only ever look at one plant at a time. This is why the
  backend's achievable VC improvement is often smaller (but real) compared
  to the preview's more optimistic (but not actually achievable) estimate.
- **The root CLI (`main.py`) is a standalone tool**, not connected to the
  dashboard or the backend. It has its own, separate, per-plant OR-Tools
  attempt with a greedy fallback if OR-Tools is unavailable - but it never
  presents that fallback as equivalent to an official result; every
  fallback row is explicitly labeled `"Greedy (PREVIEW - not official)"` in
  both its console output and `output/output.xlsx`.

## Editing the dashboard

Always edit `dashboard_template.html`, then regenerate:
```bash
python main.py --dashboard dashboard_template.html
```
This keeps `output/index.html`'s embedded data in sync with `input.xlsx`
without you having to hand-edit the generated file.

## Known assumptions / things to verify against Sir's Excel

- **Rakes are strictly whole numbers everywhere.** A rake is a physical
  unit - `0.5` or `52.25` rakes never exist. Every rake input (Excel,
  daily-variation CSV, `--freeze` values, dashboard Current Rakes / Actual
  Rakes, API `current_rakes` / `minRakes` / `maxRakes`) is **rejected**
  when fractional or negative - never rounded, never truncated - and every
  rake output (optimized rakes, delta rakes, plant/company/global totals)
  is a whole number. Only VC, percentages, rates, and costs legitimately
  carry decimals.
- Zero-current-allocation sources get a fallback upper bound of 50% of the
  plant's total rakes in the client-side heuristic optimizer (no explicit
  company-wide cap was available for those rows) - see `dashboard_template.html`
  `optimizePlant()`.
- The OR-Tools backend (`backend/optimizer.py`) implements exactly the 5
  constraints from the spec (company conservation, plant total 80-110%,
  plant-company bounds from the sheet, global total conservation, integer
  allocation) - nothing hardcoded, every number comes from the solver.
