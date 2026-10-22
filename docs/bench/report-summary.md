# Bench report

## Headline

660 cases, 628 ok, 32 replay_failed, 0 timeout, 0 error. 136 flagged, 2 confirmed. Cost 4.702.

Mined flagged: TP 67, FP 69, FN 250, TN 242.
Hand flagged: TP 0, FP 73, FN 3, TN 273.

The confidence strong slice includes broke cases with confidence revert, explicit-merge-fix, or medium, plus every clean case from those same repos.

660 cases. Cost 4.702 (mean 0.007, median 0.001). Seconds 20704.124 (mean 31.370, median 8.713).

## Non-ok statuses

| repo | replay_failed | timeout | error | other |
|---|---:|---:|---:|---:|
| PyCQA__isort | 3 | 0 | 0 | 0 |
| aio-libs__multidict | 2 | 0 | 0 | 0 |
| marshmallow-code__marshmallow | 9 | 0 | 0 | 0 |
| pallets__click | 1 | 0 | 0 | 0 |
| pallets__flask | 2 | 0 | 0 | 0 |
| pallets__jinja | 1 | 0 | 0 | 0 |
| pallets__werkzeug | 10 | 0 | 0 | 0 |
| psf__black | 1 | 0 | 0 | 0 |
| python-jsonschema__jsonschema | 1 | 0 | 0 | 0 |
| tqdm__tqdm | 2 | 0 | 0 | 0 |

## Mined ground truth

### Flagged (high or test-confirmed)

| slice | n | TP | FP | FN | TN | precision | recall | specificity | F1 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| overall | 628 | 67 | 69 | 250 | 242 | 0.493 | 0.211 | 0.778 | 0.296 |
| repo Delgan__loguru | 2 | 0 | 0 | 1 | 1 | - | 0.000 | 1.000 | - |
| repo PyCQA__isort | 43 | 0 | 3 | 20 | 20 | 0.000 | 0.000 | 0.870 | - |
| repo Textualize__rich | 2 | 0 | 0 | 1 | 1 | - | 0.000 | 1.000 | - |
| repo aio-libs__multidict | 16 | 7 | 4 | 2 | 3 | 0.636 | 0.778 | 0.429 | 0.700 |
| repo aio-libs__yarl | 16 | 1 | 1 | 7 | 7 | 0.500 | 0.125 | 0.875 | 0.200 |
| repo arrow-py__arrow | 12 | 1 | 0 | 5 | 6 | 1.000 | 0.167 | 1.000 | 0.286 |
| repo axios__axios | 4 | 0 | 0 | 2 | 2 | - | 0.000 | 1.000 | - |
| repo colinhacks__zod | 156 | 17 | 20 | 61 | 58 | 0.459 | 0.218 | 0.744 | 0.296 |
| repo dateutil__dateutil | 10 | 0 | 1 | 5 | 4 | 0.000 | 0.000 | 0.800 | - |
| repo encode__httpx | 20 | 0 | 0 | 10 | 10 | - | 0.000 | 1.000 | - |
| repo encode__starlette | 4 | 0 | 0 | 2 | 2 | - | 0.000 | 1.000 | - |
| repo encode__uvicorn | 20 | 1 | 2 | 9 | 8 | 0.333 | 0.100 | 0.800 | 0.154 |
| repo fastapi__fastapi | 22 | 0 | 0 | 11 | 11 | - | 0.000 | 1.000 | - |
| repo fastapi__sqlmodel | 8 | 0 | 2 | 4 | 2 | 0.000 | 0.000 | 0.500 | - |
| repo honojs__hono | 28 | 4 | 6 | 10 | 8 | 0.400 | 0.286 | 0.571 | 0.333 |
| repo httpie__cli | 4 | 0 | 0 | 2 | 2 | - | 0.000 | 1.000 | - |
| repo hynek__structlog | 2 | 1 | 1 | 0 | 0 | 0.500 | 1.000 | 0.000 | 0.667 |
| repo jazzband__pip-tools | 10 | 1 | 1 | 4 | 4 | 0.500 | 0.200 | 0.800 | 0.286 |
| repo marshmallow-code__marshmallow | 15 | 3 | 0 | 6 | 6 | 1.000 | 0.333 | 1.000 | 0.500 |
| repo more-itertools__more-itertools | 10 | 0 | 0 | 5 | 5 | - | 0.000 | 1.000 | - |
| repo pallets__click | 15 | 3 | 2 | 4 | 6 | 0.600 | 0.429 | 0.750 | 0.500 |
| repo pallets__flask | 8 | 0 | 0 | 4 | 4 | - | 0.000 | 1.000 | - |
| repo pallets__jinja | 3 | 0 | 0 | 2 | 1 | - | 0.000 | 1.000 | - |
| repo pallets__werkzeug | 10 | 0 | 1 | 7 | 2 | 0.000 | 0.000 | 0.667 | - |
| repo psf__black | 99 | 19 | 18 | 30 | 32 | 0.514 | 0.388 | 0.640 | 0.442 |
| repo psf__requests | 12 | 0 | 0 | 6 | 6 | - | 0.000 | 1.000 | - |
| repo pydantic__pydantic-settings | 42 | 8 | 6 | 13 | 15 | 0.571 | 0.381 | 0.714 | 0.457 |
| repo pytest-dev__pluggy | 6 | 0 | 0 | 3 | 3 | - | 0.000 | 1.000 | - |
| repo python-attrs__attrs | 8 | 0 | 1 | 4 | 3 | 0.000 | 0.000 | 0.750 | - |
| repo python-jsonschema__jsonschema | 5 | 0 | 0 | 2 | 3 | - | 0.000 | 1.000 | - |
| repo sindresorhus__ky | 12 | 1 | 0 | 5 | 6 | 1.000 | 0.167 | 1.000 | 0.286 |
| repo tqdm__tqdm | 4 | 0 | 0 | 3 | 1 | - | 0.000 | 1.000 | - |
| language python | 428 | 45 | 43 | 172 | 168 | 0.511 | 0.207 | 0.796 | 0.295 |
| language ts | 200 | 22 | 26 | 78 | 74 | 0.458 | 0.220 | 0.740 | 0.297 |
| confidence medium | 10 | 3 | 0 | 7 | 0 | 1.000 | 0.300 | - | 0.462 |
| confidence none | 311 | 0 | 69 | 0 | 242 | 0.000 | - | 0.778 | - |
| confidence revert | 6 | 1 | 0 | 5 | 0 | 1.000 | 0.167 | - | 0.286 |
| confidence strong | 185 | 4 | 42 | 12 | 127 | 0.087 | 0.250 | 0.751 | 0.129 |
| confidence weak | 301 | 63 | 0 | 238 | 0 | 1.000 | 0.209 | - | 0.346 |

### Confirmed (test-confirmed only)

| slice | n | TP | FP | FN | TN | precision | recall | specificity | F1 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| overall | 628 | 0 | 2 | 317 | 309 | 0.000 | 0.000 | 0.994 | - |
| repo Delgan__loguru | 2 | 0 | 0 | 1 | 1 | - | 0.000 | 1.000 | - |
| repo PyCQA__isort | 43 | 0 | 0 | 20 | 23 | - | 0.000 | 1.000 | - |
| repo Textualize__rich | 2 | 0 | 0 | 1 | 1 | - | 0.000 | 1.000 | - |
| repo aio-libs__multidict | 16 | 0 | 1 | 9 | 6 | 0.000 | 0.000 | 0.857 | - |
| repo aio-libs__yarl | 16 | 0 | 0 | 8 | 8 | - | 0.000 | 1.000 | - |
| repo arrow-py__arrow | 12 | 0 | 0 | 6 | 6 | - | 0.000 | 1.000 | - |
| repo axios__axios | 4 | 0 | 0 | 2 | 2 | - | 0.000 | 1.000 | - |
| repo colinhacks__zod | 156 | 0 | 0 | 78 | 78 | - | 0.000 | 1.000 | - |
| repo dateutil__dateutil | 10 | 0 | 0 | 5 | 5 | - | 0.000 | 1.000 | - |
| repo encode__httpx | 20 | 0 | 0 | 10 | 10 | - | 0.000 | 1.000 | - |
| repo encode__starlette | 4 | 0 | 0 | 2 | 2 | - | 0.000 | 1.000 | - |
| repo encode__uvicorn | 20 | 0 | 0 | 10 | 10 | - | 0.000 | 1.000 | - |
| repo fastapi__fastapi | 22 | 0 | 0 | 11 | 11 | - | 0.000 | 1.000 | - |
| repo fastapi__sqlmodel | 8 | 0 | 0 | 4 | 4 | - | 0.000 | 1.000 | - |
| repo honojs__hono | 28 | 0 | 0 | 14 | 14 | - | 0.000 | 1.000 | - |
| repo httpie__cli | 4 | 0 | 0 | 2 | 2 | - | 0.000 | 1.000 | - |
| repo hynek__structlog | 2 | 0 | 0 | 1 | 1 | - | 0.000 | 1.000 | - |
| repo jazzband__pip-tools | 10 | 0 | 0 | 5 | 5 | - | 0.000 | 1.000 | - |
| repo marshmallow-code__marshmallow | 15 | 0 | 0 | 9 | 6 | - | 0.000 | 1.000 | - |
| repo more-itertools__more-itertools | 10 | 0 | 0 | 5 | 5 | - | 0.000 | 1.000 | - |
| repo pallets__click | 15 | 0 | 0 | 7 | 8 | - | 0.000 | 1.000 | - |
| repo pallets__flask | 8 | 0 | 0 | 4 | 4 | - | 0.000 | 1.000 | - |
| repo pallets__jinja | 3 | 0 | 0 | 2 | 1 | - | 0.000 | 1.000 | - |
| repo pallets__werkzeug | 10 | 0 | 0 | 7 | 3 | - | 0.000 | 1.000 | - |
| repo psf__black | 99 | 0 | 0 | 49 | 50 | - | 0.000 | 1.000 | - |
| repo psf__requests | 12 | 0 | 0 | 6 | 6 | - | 0.000 | 1.000 | - |
| repo pydantic__pydantic-settings | 42 | 0 | 1 | 21 | 20 | 0.000 | 0.000 | 0.952 | - |
| repo pytest-dev__pluggy | 6 | 0 | 0 | 3 | 3 | - | 0.000 | 1.000 | - |
| repo python-attrs__attrs | 8 | 0 | 0 | 4 | 4 | - | 0.000 | 1.000 | - |
| repo python-jsonschema__jsonschema | 5 | 0 | 0 | 2 | 3 | - | 0.000 | 1.000 | - |
| repo sindresorhus__ky | 12 | 0 | 0 | 6 | 6 | - | 0.000 | 1.000 | - |
| repo tqdm__tqdm | 4 | 0 | 0 | 3 | 1 | - | 0.000 | 1.000 | - |
| language python | 428 | 0 | 2 | 217 | 209 | 0.000 | 0.000 | 0.991 | - |
| language ts | 200 | 0 | 0 | 100 | 100 | - | 0.000 | 1.000 | - |
| confidence medium | 10 | 0 | 0 | 10 | 0 | - | 0.000 | - | - |
| confidence none | 311 | 0 | 2 | 0 | 309 | 0.000 | - | 0.994 | - |
| confidence revert | 6 | 0 | 0 | 6 | 0 | - | 0.000 | - | - |
| confidence strong | 185 | 0 | 0 | 16 | 169 | - | 0.000 | 1.000 | - |
| confidence weak | 301 | 0 | 0 | 301 | 0 | - | 0.000 | - | - |


## Hand-checked ground truth

### Flagged (high or test-confirmed)

| slice | n | TP | FP | FN | TN | precision | recall | specificity | F1 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| overall | 349 | 0 | 73 | 3 | 273 | 0.000 | 0.000 | 0.789 | - |
| repo Delgan__loguru | 1 | 0 | 0 | 0 | 1 | - | - | 1.000 | - |
| repo PyCQA__isort | 23 | 0 | 3 | 0 | 20 | 0.000 | - | 0.870 | - |
| repo Textualize__rich | 2 | 0 | 0 | 0 | 2 | - | - | 1.000 | - |
| repo aio-libs__multidict | 8 | 0 | 5 | 0 | 3 | 0.000 | - | 0.375 | - |
| repo aio-libs__yarl | 9 | 0 | 1 | 0 | 8 | 0.000 | - | 0.889 | - |
| repo arrow-py__arrow | 6 | 0 | 0 | 0 | 6 | - | - | 1.000 | - |
| repo axios__axios | 3 | 0 | 0 | 0 | 3 | - | - | 1.000 | - |
| repo colinhacks__zod | 83 | 0 | 21 | 0 | 62 | 0.000 | - | 0.747 | - |
| repo dateutil__dateutil | 5 | 0 | 1 | 0 | 4 | 0.000 | - | 0.800 | - |
| repo encode__httpx | 11 | 0 | 0 | 0 | 11 | - | - | 1.000 | - |
| repo encode__starlette | 2 | 0 | 0 | 0 | 2 | - | - | 1.000 | - |
| repo encode__uvicorn | 11 | 0 | 2 | 0 | 9 | 0.000 | - | 0.818 | - |
| repo fastapi__fastapi | 21 | 0 | 0 | 1 | 20 | - | 0.000 | 1.000 | - |
| repo fastapi__sqlmodel | 4 | 0 | 2 | 0 | 2 | 0.000 | - | 0.500 | - |
| repo honojs__hono | 14 | 0 | 6 | 0 | 8 | 0.000 | - | 0.571 | - |
| repo httpie__cli | 2 | 0 | 0 | 0 | 2 | - | - | 1.000 | - |
| repo hynek__structlog | 1 | 0 | 1 | 0 | 0 | 0.000 | - | 0.000 | - |
| repo jazzband__pip-tools | 5 | 0 | 1 | 0 | 4 | 0.000 | - | 0.800 | - |
| repo marshmallow-code__marshmallow | 6 | 0 | 0 | 0 | 6 | - | - | 1.000 | - |
| repo more-itertools__more-itertools | 5 | 0 | 0 | 0 | 5 | - | - | 1.000 | - |
| repo pallets__click | 9 | 0 | 2 | 0 | 7 | 0.000 | - | 0.778 | - |
| repo pallets__flask | 4 | 0 | 0 | 0 | 4 | - | - | 1.000 | - |
| repo pallets__jinja | 1 | 0 | 0 | 0 | 1 | - | - | 1.000 | - |
| repo pallets__werkzeug | 4 | 0 | 1 | 0 | 3 | 0.000 | - | 0.750 | - |
| repo psf__black | 53 | 0 | 19 | 2 | 32 | 0.000 | 0.000 | 0.627 | - |
| repo psf__requests | 10 | 0 | 0 | 0 | 10 | - | - | 1.000 | - |
| repo pydantic__pydantic-settings | 23 | 0 | 7 | 0 | 16 | 0.000 | - | 0.696 | - |
| repo pytest-dev__pluggy | 3 | 0 | 0 | 0 | 3 | - | - | 1.000 | - |
| repo python-attrs__attrs | 5 | 0 | 1 | 0 | 4 | 0.000 | - | 0.800 | - |
| repo python-jsonschema__jsonschema | 3 | 0 | 0 | 0 | 3 | - | - | 1.000 | - |
| repo sindresorhus__ky | 11 | 0 | 0 | 0 | 11 | - | - | 1.000 | - |
| repo tqdm__tqdm | 1 | 0 | 0 | 0 | 1 | - | - | 1.000 | - |
| language python | 238 | 0 | 46 | 3 | 189 | 0.000 | 0.000 | 0.804 | - |
| language ts | 111 | 0 | 27 | 0 | 84 | 0.000 | - | 0.757 | - |
| confidence medium | 9 | 0 | 2 | 2 | 5 | 0.000 | 0.000 | 0.714 | - |
| confidence none | 311 | 0 | 69 | 0 | 242 | 0.000 | - | 0.778 | - |
| confidence revert | 2 | 0 | 0 | 0 | 2 | - | - | 1.000 | - |
| confidence strong | 53 | 0 | 19 | 2 | 32 | 0.000 | 0.000 | 0.627 | - |
| confidence weak | 27 | 0 | 2 | 1 | 24 | 0.000 | 0.000 | 0.923 | - |

### Confirmed (test-confirmed only)

| slice | n | TP | FP | FN | TN | precision | recall | specificity | F1 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| overall | 349 | 0 | 2 | 3 | 344 | 0.000 | 0.000 | 0.994 | - |
| repo Delgan__loguru | 1 | 0 | 0 | 0 | 1 | - | - | 1.000 | - |
| repo PyCQA__isort | 23 | 0 | 0 | 0 | 23 | - | - | 1.000 | - |
| repo Textualize__rich | 2 | 0 | 0 | 0 | 2 | - | - | 1.000 | - |
| repo aio-libs__multidict | 8 | 0 | 1 | 0 | 7 | 0.000 | - | 0.875 | - |
| repo aio-libs__yarl | 9 | 0 | 0 | 0 | 9 | - | - | 1.000 | - |
| repo arrow-py__arrow | 6 | 0 | 0 | 0 | 6 | - | - | 1.000 | - |
| repo axios__axios | 3 | 0 | 0 | 0 | 3 | - | - | 1.000 | - |
| repo colinhacks__zod | 83 | 0 | 0 | 0 | 83 | - | - | 1.000 | - |
| repo dateutil__dateutil | 5 | 0 | 0 | 0 | 5 | - | - | 1.000 | - |
| repo encode__httpx | 11 | 0 | 0 | 0 | 11 | - | - | 1.000 | - |
| repo encode__starlette | 2 | 0 | 0 | 0 | 2 | - | - | 1.000 | - |
| repo encode__uvicorn | 11 | 0 | 0 | 0 | 11 | - | - | 1.000 | - |
| repo fastapi__fastapi | 21 | 0 | 0 | 1 | 20 | - | 0.000 | 1.000 | - |
| repo fastapi__sqlmodel | 4 | 0 | 0 | 0 | 4 | - | - | 1.000 | - |
| repo honojs__hono | 14 | 0 | 0 | 0 | 14 | - | - | 1.000 | - |
| repo httpie__cli | 2 | 0 | 0 | 0 | 2 | - | - | 1.000 | - |
| repo hynek__structlog | 1 | 0 | 0 | 0 | 1 | - | - | 1.000 | - |
| repo jazzband__pip-tools | 5 | 0 | 0 | 0 | 5 | - | - | 1.000 | - |
| repo marshmallow-code__marshmallow | 6 | 0 | 0 | 0 | 6 | - | - | 1.000 | - |
| repo more-itertools__more-itertools | 5 | 0 | 0 | 0 | 5 | - | - | 1.000 | - |
| repo pallets__click | 9 | 0 | 0 | 0 | 9 | - | - | 1.000 | - |
| repo pallets__flask | 4 | 0 | 0 | 0 | 4 | - | - | 1.000 | - |
| repo pallets__jinja | 1 | 0 | 0 | 0 | 1 | - | - | 1.000 | - |
| repo pallets__werkzeug | 4 | 0 | 0 | 0 | 4 | - | - | 1.000 | - |
| repo psf__black | 53 | 0 | 0 | 2 | 51 | - | 0.000 | 1.000 | - |
| repo psf__requests | 10 | 0 | 0 | 0 | 10 | - | - | 1.000 | - |
| repo pydantic__pydantic-settings | 23 | 0 | 1 | 0 | 22 | 0.000 | - | 0.957 | - |
| repo pytest-dev__pluggy | 3 | 0 | 0 | 0 | 3 | - | - | 1.000 | - |
| repo python-attrs__attrs | 5 | 0 | 0 | 0 | 5 | - | - | 1.000 | - |
| repo python-jsonschema__jsonschema | 3 | 0 | 0 | 0 | 3 | - | - | 1.000 | - |
| repo sindresorhus__ky | 11 | 0 | 0 | 0 | 11 | - | - | 1.000 | - |
| repo tqdm__tqdm | 1 | 0 | 0 | 0 | 1 | - | - | 1.000 | - |
| language python | 238 | 0 | 2 | 3 | 233 | 0.000 | 0.000 | 0.991 | - |
| language ts | 111 | 0 | 0 | 0 | 111 | - | - | 1.000 | - |
| confidence medium | 9 | 0 | 0 | 2 | 7 | - | 0.000 | 1.000 | - |
| confidence none | 311 | 0 | 2 | 0 | 309 | 0.000 | - | 0.994 | - |
| confidence revert | 2 | 0 | 0 | 0 | 2 | - | - | 1.000 | - |
| confidence strong | 53 | 0 | 0 | 2 | 51 | - | 0.000 | 1.000 | - |
| confidence weak | 27 | 0 | 0 | 1 | 26 | - | 0.000 | 1.000 | - |


## Top catches

Ground truth: hand.

No true positives.

## Worst misses

Ground truth: hand.

- `psf__black-658c8d8d-aafc21aa` (not flagged, medium) [A](https://github.com/psf/black/commit/658c8d8d96047c5ba77be4aecc2545a22d5e35b9) [B](https://github.com/psf/black/commit/aafc21aa77f5c4d2ebcb833aa60faba6c2138b94) [fix](https://github.com/psf/black/commit/78163939f157d9e18a8c0528fc5e1c58b1c1e69c). verification: -; branch tests: neither branch added or modified a test file
- `psf__black-c26c7728-2918ea3b` (not flagged, medium) [A](https://github.com/psf/black/commit/c26c7728e883db1425f7ed7affec41da3b3200a3) [B](https://github.com/psf/black/commit/2918ea3b079bbb617b2f9f0d5bc0b84fde04e48e) [fix](https://github.com/psf/black/commit/9b161072c13c0ec32c9ca9bd48fad17f781a56d4). verification: -; branch tests: no branch test passed on its own branch, nothing to compare
- `fastapi__fastapi-73c411e1-98244866` (not flagged, weak) [A](https://github.com/fastapi/fastapi/commit/73c411e1b92a1659fef76655562e6d2f28be064e) [B](https://github.com/fastapi/fastapi/commit/982448661630e2f412f18a192eed29d0da4a8fb1) [fix](https://github.com/fastapi/fastapi/commit/bba4d4c95e0de8fc9c99f88c269909d37d64d494). verification: -; branch tests: -

## False alarms

Ground truth: hand.

- `aio-libs__multidict-c53246f9-408f3513` (confirmed, none) [A](https://github.com/aio-libs/multidict/commit/c53246f9be6d74c37136c56a002b31a99a44b29e) [B](https://github.com/aio-libs/multidict/commit/408f3513004fc5ab9ef2f5b3f9259d93ae56599f). verification: 1 intent test(s) that pass on their own branch and capture its change fail on the merge of A+B; branch tests: no branch test passed on its own branch, nothing to compare
- `pydantic__pydantic-settings-a853a132-c2fd92ff` (confirmed, none) [A](https://github.com/pydantic/pydantic-settings/commit/a853a132cfeeb69b72c8c9f0378f860e9becfd3f) [B](https://github.com/pydantic/pydantic-settings/commit/c2fd92ffa74b14ed38fd8fcb1f77d087ca4a960e). verification: merge only fails tests that pin unchanged base behaviour; branch tests: 1 test(s) from the branches' own test files pass on their branch and fail on the merge of A+B (not on the merge-base, twice on the merge), e.g. tests.test_settings
- `colinhacks__zod-45fdeda5-b1077f05` (flagged, medium) [A](https://github.com/colinhacks/zod/commit/45fdeda54fc610261f0d18acaf869f75d568487e) [B](https://github.com/colinhacks/zod/commit/b1077f05f85450cf53108714973bf7c8d9e3c35d) [fix](https://github.com/colinhacks/zod/commit/9f0a3d81221e3ab7c09ca4911ef35b54817869a4). verification: no Python changes on either branch; branch tests: textual merge conflict: git merge --no-commit failed
- `psf__black-fc6cea0f-d7a28dd7` (flagged, medium) [A](https://github.com/psf/black/commit/fc6cea0f0e0f6da99fffbedbfcf3d50cc0e641a4) [B](https://github.com/psf/black/commit/d7a28dd78631fb962da95fb2d2de0e18ca6754a4) [fix](https://github.com/psf/black/commit/dba3c2695c59fdb11825dbdf8f3b0ab6e0b368b2). verification: generated test does not pass on branch A and B; branch tests: no branch test passed on its own branch, nothing to compare
- `aio-libs__multidict-8f0f7543-b36706a3` (flagged, weak) [A](https://github.com/aio-libs/multidict/commit/8f0f75439acd1b14a555e2ab571b7e639e24169e) [B](https://github.com/aio-libs/multidict/commit/b36706a3b62eb11df4965a924c06786904b02682) [fix](https://github.com/aio-libs/multidict/commit/0a4d62ddcdb264e984fbe521deee088f556040f5). verification: generated test does not pass on branch A and B; branch tests: textual merge conflict: git merge --no-commit failed
- `pydantic__pydantic-settings-8af81c8d-d51a8229` (flagged, weak) [A](https://github.com/pydantic/pydantic-settings/commit/8af81c8d0f968420670a36a9c05efced004b0b53) [B](https://github.com/pydantic/pydantic-settings/commit/d51a82296be247ea4a192ce4a885426662f3abef) [fix](https://github.com/pydantic/pydantic-settings/commit/632439e1177c650b9bf22194ce4995ebc603cafa). verification: generated test does not pass on branch A and B; branch tests: textual merge conflict: git merge --no-commit failed
- `PyCQA__isort-22329547-80c213b2` (flagged, none) [A](https://github.com/PyCQA/isort/commit/2232954711b716434fbbf6e42e586d8b302c7e64) [B](https://github.com/PyCQA/isort/commit/80c213b2d5d95350ce5dc0d7bbc4dc0065435bd9). verification: generated test does not pass on branch A and B; branch tests: no branch test passed on its own branch, nothing to compare
- `PyCQA__isort-473d150c-3f87900c` (flagged, none) [A](https://github.com/PyCQA/isort/commit/473d150c1db60f4614a574e30993e9e0d3ca0cce) [B](https://github.com/PyCQA/isort/commit/3f87900cb5391898e5fb4ceac939284cf9cf19ca). verification: generated test does not pass on branch A and B; branch tests: textual merge conflict: git merge --no-commit failed
- `PyCQA__isort-b75358b1-b8c2f922` (flagged, none) [A](https://github.com/PyCQA/isort/commit/b75358b10a4e9357c19d5e7a2ced79f14bdb6280) [B](https://github.com/PyCQA/isort/commit/b8c2f922aadbd23be8f543ead7a826214d04b9c7). verification: generated test does not pass on branch A and B; branch tests: neither branch added or modified a test file
- `aio-libs__multidict-51c75e40-ed928afe` (flagged, none) [A](https://github.com/aio-libs/multidict/commit/51c75e40054dd5cbcb8bb4abbb0a6a0f4d806024) [B](https://github.com/aio-libs/multidict/commit/ed928afe88b00396127ac51ff4a7454d094ac8bf). verification: textual merge conflict: git merge --no-commit failed; branch tests: textual merge conflict: git merge --no-commit failed
- `aio-libs__multidict-598bcb81-5630f174` (flagged, none) [A](https://github.com/aio-libs/multidict/commit/598bcb81a71f99778232ebf53412979926ace9d4) [B](https://github.com/aio-libs/multidict/commit/5630f1744d3bcb052217a09a4725889d2eaf0ee6). verification: generated test does not pass on branch A and B; branch tests: textual merge conflict: git merge --no-commit failed
- `aio-libs__multidict-f08f7689-62df00f1` (flagged, none) [A](https://github.com/aio-libs/multidict/commit/f08f76892cb90457a9f6b3667da464b67a5c0e9d) [B](https://github.com/aio-libs/multidict/commit/62df00f1ec597e68fe997c965443b7521fa7ffdb). verification: generated test does not pass on branch A and B; branch tests: no branch test passed on its own branch, nothing to compare
- `aio-libs__yarl-61cee462-5cc9f309` (flagged, none) [A](https://github.com/aio-libs/yarl/commit/61cee462895ebe79259feada991d2af89790d175) [B](https://github.com/aio-libs/yarl/commit/5cc9f30992052b97c96ae94aaa337a4d3b0ac637). verification: generated test does not pass on branch A and B; branch tests: textual merge conflict: git merge --no-commit failed
- `colinhacks__zod-09699501-36fef584` (flagged, none) [A](https://github.com/colinhacks/zod/commit/09699501ff6218b3b0a7e382eca3c02a8226ce13) [B](https://github.com/colinhacks/zod/commit/36fef58410f4b2c9e79edabae2fc567a4aee13a7). verification: no Python changes on either branch; branch tests: textual merge conflict: git merge --no-commit failed
- `colinhacks__zod-15de22a3-36fef584` (flagged, none) [A](https://github.com/colinhacks/zod/commit/15de22a3ba6144c7d8d2276e8e56174bcdfa7225) [B](https://github.com/colinhacks/zod/commit/36fef58410f4b2c9e79edabae2fc567a4aee13a7). verification: no Python changes on either branch; branch tests: textual merge conflict: git merge --no-commit failed
- `colinhacks__zod-30bcf8e3-0ca48b28` (flagged, none) [A](https://github.com/colinhacks/zod/commit/30bcf8e3bf393556a1be96e0a2ef34fac9ac9c35) [B](https://github.com/colinhacks/zod/commit/0ca48b285942edbf044d7fbdc9527d618e6eeef4). verification: no Python changes on either branch; branch tests: textual merge conflict: git merge --no-commit failed
- `colinhacks__zod-35f0a380-0ca48b28` (flagged, none) [A](https://github.com/colinhacks/zod/commit/35f0a3804fdad33c8c5cb659aaf9a6ce922038a2) [B](https://github.com/colinhacks/zod/commit/0ca48b285942edbf044d7fbdc9527d618e6eeef4). verification: no Python changes on either branch; branch tests: textual merge conflict: git merge --no-commit failed
- `colinhacks__zod-37e9c550-36fef584` (flagged, none) [A](https://github.com/colinhacks/zod/commit/37e9c550460e4edd144da90d903e878c119c5cc1) [B](https://github.com/colinhacks/zod/commit/36fef58410f4b2c9e79edabae2fc567a4aee13a7). verification: no Python changes on either branch; branch tests: branch tests could not run: JS setup failed: yarn not found (next to /usr/bin/node, Homebrew, /usr/local/bin)
- `colinhacks__zod-3c5ffd24-efbcd7a8` (flagged, none) [A](https://github.com/colinhacks/zod/commit/3c5ffd24887e7ae2ffa9fb9fe12cd661cc40d873) [B](https://github.com/colinhacks/zod/commit/efbcd7a866241d5e7f179aac8bf1a44e801e77ca). verification: no Python changes on either branch; branch tests: neither branch added or modified a test file
- `colinhacks__zod-41a70c38-c8a96224` (flagged, none) [A](https://github.com/colinhacks/zod/commit/41a70c38a2526c62c31b1cbc4161fb596a158c0a) [B](https://github.com/colinhacks/zod/commit/c8a96224d869a13852a462132bf8e0184ba6c206). verification: no Python changes on either branch; branch tests: branch tests could not run: JS setup failed: yarn not found (next to /usr/bin/node, Homebrew, /usr/local/bin)
- `colinhacks__zod-485abbf7-0ca48b28` (flagged, none) [A](https://github.com/colinhacks/zod/commit/485abbf7e113b672c2e9f348165669e4c686b1af) [B](https://github.com/colinhacks/zod/commit/0ca48b285942edbf044d7fbdc9527d618e6eeef4). verification: no Python changes on either branch; branch tests: textual merge conflict: git merge --no-commit failed
- `colinhacks__zod-60a21346-36fef584` (flagged, none) [A](https://github.com/colinhacks/zod/commit/60a21346086d32ca9f39efc2771f5db37c835c03) [B](https://github.com/colinhacks/zod/commit/36fef58410f4b2c9e79edabae2fc567a4aee13a7). verification: no Python changes on either branch; branch tests: textual merge conflict: git merge --no-commit failed
- `colinhacks__zod-79189734-efbcd7a8` (flagged, none) [A](https://github.com/colinhacks/zod/commit/7918973408e85767143a779c26044108f776350f) [B](https://github.com/colinhacks/zod/commit/efbcd7a866241d5e7f179aac8bf1a44e801e77ca). verification: no Python changes on either branch; branch tests: neither branch added or modified a test file
- `colinhacks__zod-8d0a4b36-0ca48b28` (flagged, none) [A](https://github.com/colinhacks/zod/commit/8d0a4b36f2a38445f62e81a8ed2c8c7ae22316c5) [B](https://github.com/colinhacks/zod/commit/0ca48b285942edbf044d7fbdc9527d618e6eeef4). verification: -; branch tests: branch tests could not run: JS setup failed: yarn not found (next to /usr/bin/node, Homebrew, /usr/local/bin)
- `colinhacks__zod-9894ce31-0ca48b28` (flagged, none) [A](https://github.com/colinhacks/zod/commit/9894ce3140440bc7d2c53b213d016c751f08c6ec) [B](https://github.com/colinhacks/zod/commit/0ca48b285942edbf044d7fbdc9527d618e6eeef4). verification: no Python changes on either branch; branch tests: textual merge conflict: git merge --no-commit failed
- `colinhacks__zod-9cc890d5-0ca48b28` (flagged, none) [A](https://github.com/colinhacks/zod/commit/9cc890d56ecd644e4e05890e95f824a9307a0995) [B](https://github.com/colinhacks/zod/commit/0ca48b285942edbf044d7fbdc9527d618e6eeef4). verification: no Python changes on either branch; branch tests: textual merge conflict: git merge --no-commit failed
- `colinhacks__zod-9cc890d5-c54e1099` (flagged, none) [A](https://github.com/colinhacks/zod/commit/9cc890d56ecd644e4e05890e95f824a9307a0995) [B](https://github.com/colinhacks/zod/commit/c54e1099a28793368ef6302cdcc1f95201b49c69). verification: no Python changes on either branch; branch tests: textual merge conflict: git merge --no-commit failed
- `colinhacks__zod-af083901-36fef584` (flagged, none) [A](https://github.com/colinhacks/zod/commit/af08390139cf9fd4fc9e398b60a39191bf224076) [B](https://github.com/colinhacks/zod/commit/36fef58410f4b2c9e79edabae2fc567a4aee13a7). verification: no Python changes on either branch; branch tests: textual merge conflict: git merge --no-commit failed
- `colinhacks__zod-bfdfe5ce-0ca48b28` (flagged, none) [A](https://github.com/colinhacks/zod/commit/bfdfe5ce6a2376e5794ac3156c74f2234f48ba9f) [B](https://github.com/colinhacks/zod/commit/0ca48b285942edbf044d7fbdc9527d618e6eeef4). verification: no Python changes on either branch; branch tests: textual merge conflict: git merge --no-commit failed
- `colinhacks__zod-c54e1099-0ca48b28` (flagged, none) [A](https://github.com/colinhacks/zod/commit/c54e1099a28793368ef6302cdcc1f95201b49c69) [B](https://github.com/colinhacks/zod/commit/0ca48b285942edbf044d7fbdc9527d618e6eeef4). verification: no Python changes on either branch; branch tests: textual merge conflict: git merge --no-commit failed
- `colinhacks__zod-dae761d6-0ca48b28` (flagged, none) [A](https://github.com/colinhacks/zod/commit/dae761d60aa1c9d02411b28d9f3e10c386acde92) [B](https://github.com/colinhacks/zod/commit/0ca48b285942edbf044d7fbdc9527d618e6eeef4). verification: no Python changes on either branch; branch tests: branch tests could not run: JS setup failed: yarn not found (next to /usr/bin/node, Homebrew, /usr/local/bin)
- `colinhacks__zod-e87db132-a8a52b3b` (flagged, none) [A](https://github.com/colinhacks/zod/commit/e87db1322f11ff6907e1789da28933d258ab75fd) [B](https://github.com/colinhacks/zod/commit/a8a52b3ba370b761be76953fa3986aa43c4172a4). verification: no Python changes on either branch; branch tests: textual merge conflict: git merge --no-commit failed
- `colinhacks__zod-ef95a73b-a8a52b3b` (flagged, none) [A](https://github.com/colinhacks/zod/commit/ef95a73b6d33299743e5ff4f0645b98c1b0d6f72) [B](https://github.com/colinhacks/zod/commit/a8a52b3ba370b761be76953fa3986aa43c4172a4). verification: no Python changes on either branch; branch tests: all 148 branch test(s) that pass on their own branch also pass on the merge (or already fail on the merge-base)
- `dateutil__dateutil-18b3fe74-f7a1d4ef` (flagged, none) [A](https://github.com/dateutil/dateutil/commit/18b3fe746ca252448f4e123f95410fc49abb0904) [B](https://github.com/dateutil/dateutil/commit/f7a1d4efa774ef3408b77c4dfd467c5225c3c4b6). verification: -; branch tests: neither branch added or modified a test file
- `encode__uvicorn-53d7d1ed-917aff08` (flagged, none) [A](https://github.com/encode/uvicorn/commit/53d7d1ed78c89f3a41937b3992c4a51ce9042021) [B](https://github.com/encode/uvicorn/commit/917aff08d5b19243aeb16f40ab9b71d8414cdf36). verification: generated test does not pass on branch A and B; branch tests: textual merge conflict: git merge --no-commit failed
- `encode__uvicorn-95b3bcd3-a877ccc6` (flagged, none) [A](https://github.com/encode/uvicorn/commit/95b3bcd335a0004a3ead4f71ee083ba98327c69f) [B](https://github.com/encode/uvicorn/commit/a877ccc63a45a0a31dcda7d1dd9c238b623602ec). verification: generated test does not pass on branch A and B; branch tests: no branch test passed on its own branch, nothing to compare
- `fastapi__sqlmodel-065fcdc8-9809b5bc` (flagged, none) [A](https://github.com/fastapi/sqlmodel/commit/065fcdc8280ab333a0e165eec9124b727d2a92e8) [B](https://github.com/fastapi/sqlmodel/commit/9809b5bc8323712fe41d1e7a92352da8a07ed69a). verification: generated test does not pass on branch A and B; branch tests: no branch test passed on its own branch, nothing to compare
- `fastapi__sqlmodel-717594ef-cbaf172c` (flagged, none) [A](https://github.com/fastapi/sqlmodel/commit/717594ef13b64d0d4fd1ce7c6305c945f68460d3) [B](https://github.com/fastapi/sqlmodel/commit/cbaf172c63889985e94031fb38ff00e80b7c90bf). verification: generated test does not pass on branch A and B; branch tests: textual merge conflict: git merge --no-commit failed
- `honojs__hono-489afe6b-d2e7440d` (flagged, none) [A](https://github.com/honojs/hono/commit/489afe6b792e4ab052b503aaa1faae46e2c53112) [B](https://github.com/honojs/hono/commit/d2e7440d85dec3385c32e10402e9d73b6a0bbc7e). verification: no Python changes on either branch; branch tests: textual merge conflict: git merge --no-commit failed
- `honojs__hono-5993b610-9488e332` (flagged, none) [A](https://github.com/honojs/hono/commit/5993b6106774459ee88bcbef3bed9641b5ce772f) [B](https://github.com/honojs/hono/commit/9488e332798c26ac6e059b994b07f08594706c3c). verification: no Python changes on either branch; branch tests: textual merge conflict: git merge --no-commit failed
- `honojs__hono-7792f5dc-e8c8c212` (flagged, none) [A](https://github.com/honojs/hono/commit/7792f5dc14acaa92056c1bf402241567ff9c7396) [B](https://github.com/honojs/hono/commit/e8c8c21244f87da80aaa5ac5ab9c92f3faca2489). verification: no Python changes on either branch; branch tests: textual merge conflict: git merge --no-commit failed
- `honojs__hono-a18edd52-e6924271` (flagged, none) [A](https://github.com/honojs/hono/commit/a18edd5294ad668bc541312eb629f756171913fa) [B](https://github.com/honojs/hono/commit/e6924271f385c1c88413501bd0c499dde1f8e3d8). verification: no Python changes on either branch; branch tests: textual merge conflict: git merge --no-commit failed
- `honojs__hono-c8f6a866-163657a4` (flagged, none) [A](https://github.com/honojs/hono/commit/c8f6a866c09ae6539d3dd636873e53d8d84f267e) [B](https://github.com/honojs/hono/commit/163657a411645562af19ee4a4ec01e7a0f5f17b1). verification: no Python changes on either branch; branch tests: textual merge conflict: git merge --no-commit failed
- `honojs__hono-e6459e73-1854e241` (flagged, none) [A](https://github.com/honojs/hono/commit/e6459e7304df223e4884bc54cbb8d1f4d759b622) [B](https://github.com/honojs/hono/commit/1854e2414557736ec375cf8df92faf2c9aff4f2c). verification: no Python changes on either branch; branch tests: textual merge conflict: git merge --no-commit failed
- `hynek__structlog-51f5d48d-448f1800` (flagged, none) [A](https://github.com/hynek/structlog/commit/51f5d48d49ea946f1f73060b3dc066f11646c6e4) [B](https://github.com/hynek/structlog/commit/448f1800ecb2f5b293b977b08fafe97b1ddb2fc5). verification: textual merge conflict: git merge --no-commit failed; branch tests: textual merge conflict: git merge --no-commit failed
- `jazzband__pip-tools-4db0eb0b-304d7ad5` (flagged, none) [A](https://github.com/jazzband/pip-tools/commit/4db0eb0b87130acf917ad32854d8227d089b3f8d) [B](https://github.com/jazzband/pip-tools/commit/304d7ad56acd921d490950d91c280c2d38e86b5a). verification: generated test does not pass on branch A and B; branch tests: no branch test passed on its own branch, nothing to compare
- `pallets__click-17874977-d8763b93` (flagged, none) [A](https://github.com/pallets/click/commit/1787497713fa389435ed732c9b26274c3cdc458d) [B](https://github.com/pallets/click/commit/d8763b93021c416549b5f8b4b5497234619410db). verification: generated test does not pass on branch A and B; branch tests: textual merge conflict: git merge --no-commit failed
- `pallets__click-27d12bd7-e4301614` (flagged, none) [A](https://github.com/pallets/click/commit/27d12bd7fbe61fcfa6d572902b01b33f6c8e0983) [B](https://github.com/pallets/click/commit/e4301614347d20b90d7fad943a52e9f19bee6be9). verification: generated test does not pass on branch A and B; branch tests: neither branch added or modified a test file
- `pallets__werkzeug-db025c61-d9f2a50a` (flagged, none) [A](https://github.com/pallets/werkzeug/commit/db025c61e752b4d422af115afc2ad1497601e024) [B](https://github.com/pallets/werkzeug/commit/d9f2a50ae446f3be6fe1094e889fd4c0d75378a4). verification: generated test does not pass on branch A and B; branch tests: textual merge conflict: git merge --no-commit failed
- `psf__black-1d233bb9-14713ac2` (flagged, none) [A](https://github.com/psf/black/commit/1d233bb977901a27c12bd883aa94eaa8f088f02c) [B](https://github.com/psf/black/commit/14713ac25fe61442775a64f4b26ef2cd6db77a8f). verification: generated test does not pass on branch A and B; branch tests: neither branch added or modified a test file
- `psf__black-268dcb67-ef6e0799` (flagged, none) [A](https://github.com/psf/black/commit/268dcb677ce80331e0ef104d8335c2ada32872fb) [B](https://github.com/psf/black/commit/ef6e079901d53a42dfae4ab10b081ce7a73a47b5). verification: generated test does not pass on branch A and B; branch tests: no branch test passed on its own branch, nothing to compare
- `psf__black-2d174df7-d7587ce9` (flagged, none) [A](https://github.com/psf/black/commit/2d174df7cb16f5dbdbb188da15fdb2059adb30d6) [B](https://github.com/psf/black/commit/d7587ce9f52f36eb41bc505e529540fd8cbdcd3e). verification: -; branch tests: textual merge conflict: git merge --no-commit failed
- `psf__black-40f4eab4-287d5709` (flagged, none) [A](https://github.com/psf/black/commit/40f4eab40a48868419b29ec42a6473371ad9c0c5) [B](https://github.com/psf/black/commit/287d570966edf07035498948206b37dfd35b51e3). verification: generated test does not pass on branch A and B; branch tests: textual merge conflict: git merge --no-commit failed
- `psf__black-6e940882-a3cb9e12` (flagged, none) [A](https://github.com/psf/black/commit/6e940882526b846e4487c5760f0127739abf9902) [B](https://github.com/psf/black/commit/a3cb9e123ceab4f0fcab4980dd6c5f9439646794). verification: generated test does not pass on branch A and B; branch tests: textual merge conflict: git merge --no-commit failed
- `psf__black-6ea4eddf-249c6536` (flagged, none) [A](https://github.com/psf/black/commit/6ea4eddf936e88c24a6757c0c858812d5ca1a9c6) [B](https://github.com/psf/black/commit/249c6536c4dff50773f30f222d1f81f0afe41f4c). verification: generated test does not pass on branch A and B; branch tests: neither branch added or modified a test file
- `psf__black-70fc1941-f0f40945` (flagged, none) [A](https://github.com/psf/black/commit/70fc194172184861aafb418c124824cae0fbe3b0) [B](https://github.com/psf/black/commit/f0f40945c1b0ebaa9ca733aca517610574c451d6). verification: generated test does not pass on branch A and B; branch tests: neither branch added or modified a test file
- `psf__black-7d062ecd-159984a7` (flagged, none) [A](https://github.com/psf/black/commit/7d062ecd5f14124a99daf452c46054ada656ad8b) [B](https://github.com/psf/black/commit/159984a7351bfc4789bc0fc85b5f408112efca85). verification: generated test does not pass on branch A and B; branch tests: neither branch added or modified a test file
- `psf__black-84556216-11cff131` (flagged, none) [A](https://github.com/psf/black/commit/845562164b8f7988ccb5b925e686150a48eeb98e) [B](https://github.com/psf/black/commit/11cff1312bf96e02bce9a467d0b5a2dd65300642). verification: generated test does not pass on branch A and B; branch tests: textual merge conflict: git merge --no-commit failed
- `psf__black-939fa93a-11cff131` (flagged, none) [A](https://github.com/psf/black/commit/939fa93a1327d2a5f2829e6c1d7e4bd25a17b0f7) [B](https://github.com/psf/black/commit/11cff1312bf96e02bce9a467d0b5a2dd65300642). verification: generated test does not pass on branch A and B; branch tests: textual merge conflict: git merge --no-commit failed
- `psf__black-c45f21d0-fe241ccc` (flagged, none) [A](https://github.com/psf/black/commit/c45f21d0399747189eedcc6c9a1614b3cf49dc0f) [B](https://github.com/psf/black/commit/fe241ccc443acbabbd75c8240d2fbc06ba4e52d6). verification: generated test does not pass on branch A and B; branch tests: neither branch added or modified a test file
- `psf__black-c47b91f5-767604e0` (flagged, none) [A](https://github.com/psf/black/commit/c47b91f513052cd39b818ea7c19716423c85c04e) [B](https://github.com/psf/black/commit/767604e03f5e454ae5b5c268cd5831c672f46de8). verification: generated test does not pass on branch A and B; branch tests: textual merge conflict: git merge --no-commit failed
- `psf__black-c9b1148b-11cff131` (flagged, none) [A](https://github.com/psf/black/commit/c9b1148b5b8799758756cbec7c0d1ac187d37f1a) [B](https://github.com/psf/black/commit/11cff1312bf96e02bce9a467d0b5a2dd65300642). verification: generated test does not pass on branch A and B; branch tests: textual merge conflict: git merge --no-commit failed
- `psf__black-d9d091de-11cff131` (flagged, none) [A](https://github.com/psf/black/commit/d9d091de04dd5872a308c8a60ca03b093c500e14) [B](https://github.com/psf/black/commit/11cff1312bf96e02bce9a467d0b5a2dd65300642). verification: generated test does not pass on branch A and B; branch tests: textual merge conflict: git merge --no-commit failed
- `psf__black-df1c86cb-a2b5ba2a` (flagged, none) [A](https://github.com/psf/black/commit/df1c86cbe7cf0727d81e526e904b0752d7371da0) [B](https://github.com/psf/black/commit/a2b5ba2a3a97eb3a0b5130a5b317bb750c1624cf). verification: -; branch tests: textual merge conflict: git merge --no-commit failed
- `psf__black-e912c7ff-966baaac` (flagged, none) [A](https://github.com/psf/black/commit/e912c7ff54c392e92a765c5eff0bd2ca3bb05b47) [B](https://github.com/psf/black/commit/966baaacbc24a4affa88ea5bb0b5a44b0f52fe2f). verification: generated test does not pass on branch A and B; branch tests: neither branch added or modified a test file
- `psf__black-fc6cea0f-ef6e0799` (flagged, none) [A](https://github.com/psf/black/commit/fc6cea0f0e0f6da99fffbedbfcf3d50cc0e641a4) [B](https://github.com/psf/black/commit/ef6e079901d53a42dfae4ab10b081ce7a73a47b5). verification: generated test does not pass on branch A and B; branch tests: no branch test passed on its own branch, nothing to compare
- `psf__black-fe241ccc-84556216` (flagged, none) [A](https://github.com/psf/black/commit/fe241ccc443acbabbd75c8240d2fbc06ba4e52d6) [B](https://github.com/psf/black/commit/845562164b8f7988ccb5b925e686150a48eeb98e). verification: generated test does not pass on branch A and B; branch tests: all 11 branch test(s) that pass on their own branch also pass on the merge (or already fail on the merge-base)
- `pydantic__pydantic-settings-284f88ee-be118241` (flagged, none) [A](https://github.com/pydantic/pydantic-settings/commit/284f88eeb6c708525ea1200216d57f93ad4e10fb) [B](https://github.com/pydantic/pydantic-settings/commit/be118241093aedf4029254b19ad3dedd41fd43f5). verification: textual merge conflict: git merge --no-commit failed; branch tests: textual merge conflict: git merge --no-commit failed
- `pydantic__pydantic-settings-343cdfe5-10dc635d` (flagged, none) [A](https://github.com/pydantic/pydantic-settings/commit/343cdfe5d950e354f5768f218eaa8396dc0d71de) [B](https://github.com/pydantic/pydantic-settings/commit/10dc635dc3160f4414e4c50b65605729274c9afc). verification: generated test does not pass on branch A and B; branch tests: textual merge conflict: git merge --no-commit failed
- `pydantic__pydantic-settings-998eb5a0-e5fb741e` (flagged, none) [A](https://github.com/pydantic/pydantic-settings/commit/998eb5a08664913a42ac1f80d1fc0e938f3bd800) [B](https://github.com/pydantic/pydantic-settings/commit/e5fb741ecb0ccb57e390218b4fbb19a34cebdc29). verification: generated test does not pass on branch A and B; branch tests: textual merge conflict: git merge --no-commit failed
- `pydantic__pydantic-settings-c50c853b-8dc9e05a` (flagged, none) [A](https://github.com/pydantic/pydantic-settings/commit/c50c853b19ec5cc673b5cf5510326ce6b2681d28) [B](https://github.com/pydantic/pydantic-settings/commit/8dc9e05ab07d91e21dd2a37d0b9c30f6d92b1930). verification: generated test does not pass on branch A; branch tests: all 469 branch test(s) that pass on their own branch also pass on the merge (or already fail on the merge-base)
- `pydantic__pydantic-settings-dba60fe0-b08fa5be` (flagged, none) [A](https://github.com/pydantic/pydantic-settings/commit/dba60fe0336f6c1759f579b4080f195f0f9f3032) [B](https://github.com/pydantic/pydantic-settings/commit/b08fa5bef3d02754adb15ebf0e69356051398f51). verification: generated test does not pass on branch A and B; branch tests: textual merge conflict: git merge --no-commit failed
- `python-attrs__attrs-08b15877-8824dc26` (flagged, none) [A](https://github.com/python-attrs/attrs/commit/08b15877c77eab4e2c0785e71f0442653b39bfe9) [B](https://github.com/python-attrs/attrs/commit/8824dc26c219abcb43564dd9386fe1a88f938344). verification: generated test does not pass on branch A and B; branch tests: textual merge conflict: git merge --no-commit failed
