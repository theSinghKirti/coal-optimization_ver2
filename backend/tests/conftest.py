import os
import sys

# backend/main.py, optimizer.py, schemas.py use flat imports (e.g.
# "from schemas import ...") that assume the backend/ directory itself is on
# sys.path - true when run via `uvicorn main:app` from inside backend/, but
# not automatically true when pytest is invoked from elsewhere. Make it true
# here so `import main` / `import optimizer` work regardless of cwd.
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)
