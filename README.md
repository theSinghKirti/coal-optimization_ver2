# UPRVUNL Coal Rake-Mix Optimizer

## Structure
```
coal-optimizer/
├── input.xlsx              # Source workbook (Dashboard / July 2026 (3) / Calculation Sheet)
├── daily_variation.csv      # Sample daily-availability overrides (--daily flag)
├── main.py                  # CLI: reads input.xlsx, reproduces Excel VC, optimizes,
│                             #      regenerates the dashboard HTML from live data
├── dashboard_template.html  # The dashboard SOURCE (edit this, not output/dashboard.html)
├── output/
│   ├── dashboard.html       # Generated: dashboard_template.html + live data from input.xlsx
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
python main.py --dashboard dashboard_template.html   # regenerate output/dashboard.html
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

Open `output/dashboard.html` directly in a browser. It's fully self-contained
(logo and data are embedded as base64/inline JSON - no external files needed
except the Google Fonts CDN link, which degrades gracefully if offline).

The "Optimize Allocation (OR-Tools)" button expects the backend above running
at `http://localhost:8000`. Override the URL by setting
`window.OPTIMIZER_API_BASE` before the dashboard's script runs, or editing the
`OPTIMIZER_API_BASE` constant near the bottom of `dashboard_template.html`.

## Editing the dashboard

Always edit `dashboard_template.html`, then regenerate:
```bash
python main.py --dashboard dashboard_template.html
```
This keeps `output/dashboard.html`'s embedded data in sync with `input.xlsx`
without you having to hand-edit the generated file.

## Known assumptions / things to verify against Sir's Excel

- Zero-current-allocation sources get a fallback upper bound of 50% of the
  plant's total rakes in the client-side heuristic optimizer (no explicit
  company-wide cap was available for those rows) - see `dashboard_template.html`
  `optimizePlant()`.
- The OR-Tools backend (`backend/optimizer.py`) implements exactly the 5
  constraints from the spec (company conservation, plant total 80-110%,
  plant-company bounds from the sheet, global total conservation, integer
  allocation) - nothing hardcoded, every number comes from the solver.
