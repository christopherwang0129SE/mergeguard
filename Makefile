# Local developer tasks. Nothing here pushes, deploys or touches CI.
PYTHON ?= python3
N ?= 5
FORCED ?= 3
MAX_USD ?= 1.0

.PHONY: eval-reliability
# Needs NEBIUS_API_KEY in the environment. Runs each case N times with no cache.
eval-reliability:
	cd backend && PYTHONPATH=. $(PYTHON) scripts/eval_reliability.py --runs $(N) --forced-runs $(FORCED) --max-usd $(MAX_USD)
