#!/usr/bin/env python3
"""Create the MergeGuard API demo: a semantic conflict that triggers Tavily.

The repo lives in ``demo/api-demo/`` (git-ignored). It is recreated from
scratch on every run:

- ``main``: ``client.py`` fetches a quote with ``requests`` (``Session``,
  ``session.mount``, ``session.get(..., timeout=...)``) and validates it with
  a pydantic model. Pricing helpers assume the payload is already in dollars.
  Tests run with ``python3 -m unittest`` and do not touch the network. If
  ``requests`` or ``pydantic`` is not installed, the tests stub them; the
  logic under test is pure. ``requirements.txt`` on main lists ``requests``
  and ``pydantic``, so every branch declares those dependencies.
- ``feat/cents-payload``: the payload is integer cents. ``normalize_amount``
  and the pydantic ``field_validator`` divide by 100, and the HTTP helper
  grows a second ``session.mount`` plus an explicit ``session.get`` timeout.
  Touches only the top of ``client.py``.
- ``feat/fee-on-cents``: ``invoice_total`` treats its input as cents and
  divides by 100 after the fee. Touches only the bottom of ``client.py``.
- ``docs/readme``: harmless branch that only edits ``README.md``.

Both feature branches change the SAME file but in far-apart regions (more
than the 3-line adjacency window), so the git heuristic reports a low risk
(1 shared file, 0 overlapping) while Nemotron sees both changes and flags
the semantic clash. The changed lines name ``requests`` and ``pydantic``
symbols, so Tavily searches the official docs.

Each branch passes its tests on its own. After merging both branches (no
textual conflict) the cents conversion runs twice and at least one test fails.

Usage:
    python3 demo/make_api_demo_repo.py            # (re)create the demo repo
    python3 demo/make_api_demo_repo.py --verify   # also verify the merge failure
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DEMO_DIR = Path(__file__).resolve().parent / "api-demo"
GIT_NAME = "MergeGuard Demo"
GIT_EMAIL = "demo@mergeguard.local"

# Fixed timestamps so a re-run produces the same commits.
DATE_MAIN = "2026-04-01T12:00:00+00:00"
DATE_CENTS = "2026-04-02T12:00:00+00:00"
DATE_FEE = "2026-04-03T12:00:00+00:00"
DATE_DOCS = "2026-04-04T12:00:00+00:00"

# ---------------------------------------------------------------------------
# client.py. The middle block is identical on every branch so the two feature
# diffs stay far apart (normalize/model/session at the top, invoice at the bottom).
# ---------------------------------------------------------------------------

HEADER = '''"""Quote client: fetch a price over HTTP, validate it, then add a fee.

``normalize_amount`` turns the raw payload into the units ``invoice_total``
expects. Those two must agree, or the invoice is off by a factor of 100.
"""

from __future__ import annotations

import requests
from pydantic import BaseModel, field_validator
from requests.adapters import HTTPAdapter

# Seconds, forwarded to requests.Session.get as timeout=.
DEFAULT_TIMEOUT = 5.0
'''

NORMALIZE_MAIN = '''
def normalize_amount(raw: float) -> float:
    """Return the payload amount unchanged.

    The quote endpoint sends major units (dollars). ``invoice_total`` expects
    those same dollars.
    """
    return round(float(raw), 2)
'''

NORMALIZE_CENTS = '''
def normalize_amount(raw: float) -> float:
    """Convert an integer-cents payload into dollars.

    ASSUMPTION: the quote endpoint sends ``amount`` as integer cents. Dividing
    by 100 here means ``invoice_total`` receives DOLLARS and must not divide
    by 100 again.
    """
    return round(float(raw) / 100.0, 2)
'''

MODEL_MAIN = '''
class Quote(BaseModel):
    """Validated quote. ``amount`` is whatever ``normalize_amount`` returns."""

    sku: str
    amount: float

    @field_validator("amount")
    @classmethod
    def apply_units(cls, value: float) -> float:
        """Store the amount in the units ``invoice_total`` expects."""
        return normalize_amount(value)
'''

MODEL_CENTS = '''
class Quote(BaseModel):
    """Validated quote. ``amount`` is dollars after the cents conversion."""

    sku: str
    amount: float

    @field_validator("amount", mode="before")
    @classmethod
    def apply_units(cls, value: float) -> float:
        """ASSUMPTION: raw JSON amount is integer cents; store dollars.

        ``invoice_total`` must treat this field as dollars. A second divide
        by 100 would bill 100.00 dollars as 1.00.
        """
        return normalize_amount(value)
'''

SESSION_MAIN = '''
def open_session() -> requests.Session:
    """HTTPS session. The adapter is mounted so retries stay explicit."""
    session = requests.Session()
    adapter = HTTPAdapter()
    session.mount("https://", adapter)
    return session


def fetch_quote(url: str, timeout: float = DEFAULT_TIMEOUT) -> Quote:
    """GET ``url`` and validate the JSON body as a Quote.

    ``timeout`` is seconds and is passed to ``session.get(..., timeout=timeout)``.
    """
    session = open_session()
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    return Quote.model_validate(response.json())
'''

SESSION_CENTS = '''
def open_session() -> requests.Session:
    """Session for the cents-payload quote endpoint.

    ASSUMPTION: callers pass an explicit timeout to ``session.get``. The
    mount does not add retries, so a missing timeout would hang the worker.
    Both schemes are mounted; quote hosts are HTTPS.
    """
    session = requests.Session()
    adapter = HTTPAdapter()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def fetch_quote(url: str, timeout: float = DEFAULT_TIMEOUT) -> Quote:
    """GET ``url`` and validate the JSON body as a Quote.

    ``timeout`` is seconds and is always passed through to requests:
    ``session.get(url, timeout=timeout)``. A non-positive timeout is rejected
    so the call cannot block.
    """
    if timeout is None or timeout <= 0:
        raise ValueError("timeout must be a positive number of seconds")
    session = open_session()
    response = session.get(url, timeout=timeout, allow_redirects=False)
    response.raise_for_status()
    return Quote.model_validate(response.json())
'''

MIDDLE = '''
def line_subtotal(price: float, qty: int) -> float:
    """Price times quantity, rounded to two decimals."""
    return round(float(price) * qty, 2)


def fee_amount(amount: float, fee_pct: float) -> float:
    """The fee alone, in the same units as ``amount``. Not a total."""
    if fee_pct <= 0:
        return 0.0
    return round(float(amount) * fee_pct / 100.0, 2)


def apply_fee(amount: float, fee_pct: float) -> float:
    """Add a percentage fee to an amount that is already in the right units."""
    if fee_pct <= 0:
        return round(float(amount), 2)
    return round(amount + fee_amount(amount, fee_pct), 2)
'''

INVOICE_MAIN = '''
def invoice_total(amount: float, qty: int, fee_pct: float) -> float:
    """Invoice total in major units: subtotal, then the fee.

    ``amount`` is dollars, matching ``normalize_amount`` on this branch.
    """
    return apply_fee(line_subtotal(amount, qty), fee_pct)
'''

INVOICE_CENTS = '''
def invoice_total(amount: float, qty: int, fee_pct: float) -> float:
    """Invoice total from a raw cents amount.

    CHANGE: ``amount`` is integer CENTS. The fee is applied in cents, then
    the result is divided by 100 to return dollars. This assumes
    ``normalize_amount`` did NOT already convert cents to dollars. A
    pre-converted dollar amount would be divided by 100 a second time
    (100.00 dollars would be billed as 1.00).
    """
    cents = line_subtotal(amount, qty)
    charged = apply_fee(cents, fee_pct)
    return round(charged / 100.0, 2)
'''


def client_source(normalize: str, model: str, session: str, invoice: str) -> str:
    return HEADER + normalize + model + session + MIDDLE + invoice


CLIENT_MAIN = client_source(NORMALIZE_MAIN, MODEL_MAIN, SESSION_MAIN, INVOICE_MAIN)
CLIENT_CENTS = client_source(NORMALIZE_CENTS, MODEL_CENTS, SESSION_CENTS, INVOICE_MAIN)
CLIENT_FEE = client_source(NORMALIZE_MAIN, MODEL_MAIN, SESSION_MAIN, INVOICE_CENTS)

# Tests stub requests/pydantic when they are not installed, then import client.
# Nothing here opens a socket. The assertions only call pure functions.
TEST_PREAMBLE = '''import sys
import types
import unittest
from unittest.mock import MagicMock


def _ensure_third_party() -> None:
    """Let ``import client`` work when requests or pydantic is not installed."""
    try:
        import requests  # noqa: F401
    except ImportError:
        requests_mod = types.ModuleType("requests")
        adapters = types.ModuleType("requests.adapters")
        adapters.HTTPAdapter = MagicMock
        requests_mod.Session = MagicMock
        requests_mod.adapters = adapters
        requests_mod.get = MagicMock()
        sys.modules["requests"] = requests_mod
        sys.modules["requests.adapters"] = adapters
    try:
        import pydantic  # noqa: F401
    except ImportError:
        pydantic_mod = types.ModuleType("pydantic")

        class BaseModel:
            def __init__(self, **kwargs):
                for key, value in kwargs.items():
                    setattr(self, key, value)

        def field_validator(*_args, **_kwargs):
            def decorator(fn):
                return fn
            return decorator

        pydantic_mod.BaseModel = BaseModel
        pydantic_mod.field_validator = field_validator
        sys.modules["pydantic"] = pydantic_mod


_ensure_third_party()
import client


'''

TESTS_MAIN = TEST_PREAMBLE + '''class ClientTests(unittest.TestCase):
    def test_line_subtotal(self):
        self.assertEqual(client.line_subtotal(10.0, 3), 30.0)

    def test_fee_amount(self):
        self.assertEqual(client.fee_amount(100.0, 10.0), 10.0)

    def test_apply_fee(self):
        self.assertEqual(client.apply_fee(100.0, 10.0), 110.0)

    def test_apply_fee_zero(self):
        self.assertEqual(client.apply_fee(100.0, 0.0), 100.0)
        self.assertEqual(client.fee_amount(100.0, 0.0), 0.0)


if __name__ == "__main__":
    unittest.main()
'''

TEST_CENTS = TEST_PREAMBLE + '''class CentsPayloadTests(unittest.TestCase):
    def test_normalize_converts_cents_to_dollars(self):
        # 10000 cents -> 100.00 dollars.
        self.assertEqual(client.normalize_amount(10000), 100.0)

    def test_invoice_uses_dollars_not_cents(self):
        # 10000 cents -> 100.00 dollars, qty 2, 10% fee -> 220.00.
        # invoice_total must NOT divide by 100 again.
        dollars = client.normalize_amount(10000)
        self.assertEqual(client.invoice_total(dollars, 2, 10.0), 220.0)


if __name__ == "__main__":
    unittest.main()
'''

TEST_FEE = TEST_PREAMBLE + '''class FeeOnCentsTests(unittest.TestCase):
    def test_normalize_still_passes_cents_through(self):
        # CHANGE assumes normalize_amount is still a pass-through, so the
        # raw cents value reaches invoice_total exactly once.
        self.assertEqual(client.normalize_amount(10000), 10000.0)

    def test_invoice_total_converts_cents_once(self):
        # 10000 cents * 2 = 20000 cents, 10% fee = 22000 cents = 220.00 dollars.
        self.assertEqual(client.invoice_total(10000, 2, 10.0), 220.0)


if __name__ == "__main__":
    unittest.main()
'''

README_MAIN = """# API Demo

A tiny quote client used by the MergeGuard demo (the one that triggers Tavily).

`client.py` fetches a quote with `requests` and validates it with pydantic.
`normalize_amount` and `invoice_total` must agree on units.

Run the tests with:

    python3 -m unittest discover -s tests
"""

README_DOCS = """# API Demo

A tiny quote client used by the MergeGuard demo (the one that triggers Tavily).

`client.py` fetches a quote with `requests` and validates it with pydantic.
`normalize_amount` and `invoice_total` must agree on units.

Run the tests with:

    python3 -m unittest discover -s tests

## Notes

- The quote endpoint sends major units (dollars), not cents.
- `normalize_amount` returns that amount unchanged.
- `invoice_total` adds the fee in dollars and does not divide by 100.
"""


def git(repo: Path, *args: str, when: str | None = None) -> str:
    env = os.environ.copy()
    if when:
        env["GIT_AUTHOR_DATE"] = when
        env["GIT_COMMITTER_DATE"] = when
    proc = subprocess.run(
        ["git", "-C", str(repo),
         "-c", f"user.name={GIT_NAME}",
         "-c", f"user.email={GIT_EMAIL}",
         "-c", "commit.gpgsign=false",
         *args],
        capture_output=True,
        text=True,
        env=env,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def create_repo() -> None:
    if DEMO_DIR.exists():
        shutil.rmtree(DEMO_DIR)
    DEMO_DIR.mkdir(parents=True)

    git(DEMO_DIR, "init", "-q", "-b", "main")

    # main
    write(DEMO_DIR / "client.py", CLIENT_MAIN)
    write(DEMO_DIR / "tests" / "test_client.py", TESTS_MAIN)
    write(DEMO_DIR / "README.md", README_MAIN)
    write(DEMO_DIR / "requirements.txt", "requests\npydantic\n")
    git(DEMO_DIR, "add", "-A")
    git(DEMO_DIR, "commit", "-q", "-m", "Add quote client with requests and pydantic",
        when=DATE_MAIN)

    # feat/cents-payload: top of client.py (normalize, validator, session)
    git(DEMO_DIR, "checkout", "-q", "-b", "feat/cents-payload")
    write(DEMO_DIR / "client.py", CLIENT_CENTS)
    write(DEMO_DIR / "tests" / "test_cents_payload.py", TEST_CENTS)
    git(DEMO_DIR, "add", "-A")
    git(DEMO_DIR, "commit", "-q", "-m",
        "ASSUMPTION: payload amount is integer cents, converted to dollars\n\n"
        "normalize_amount and the pydantic field_validator divide the HTTP\n"
        "payload by 100. invoice_total receives dollars and must not divide\n"
        "again. session.mount covers http and https; session.get takes timeout.",
        when=DATE_CENTS)

    # feat/fee-on-cents: bottom of client.py (invoice_total)
    git(DEMO_DIR, "checkout", "-q", "main")
    git(DEMO_DIR, "checkout", "-q", "-b", "feat/fee-on-cents")
    write(DEMO_DIR / "client.py", CLIENT_FEE)
    write(DEMO_DIR / "tests" / "test_fee_on_cents.py", TEST_FEE)
    git(DEMO_DIR, "add", "-A")
    git(DEMO_DIR, "commit", "-q", "-m",
        "CHANGE: invoice_total treats amount as cents and divides by 100 once\n\n"
        "The fee is applied to the raw cents amount. This assumes\n"
        "normalize_amount did not already convert cents to dollars.",
        when=DATE_FEE)

    # docs/readme
    git(DEMO_DIR, "checkout", "-q", "main")
    git(DEMO_DIR, "checkout", "-q", "-b", "docs/readme")
    write(DEMO_DIR / "README.md", README_DOCS)
    git(DEMO_DIR, "add", "-A")
    git(DEMO_DIR, "commit", "-q", "-m", "Document that the quote payload is dollars",
        when=DATE_DOCS)

    git(DEMO_DIR, "checkout", "-q", "main")
    print(f"API demo repo created at {DEMO_DIR}")
    print("Branches: main, feat/cents-payload, feat/fee-on-cents, docs/readme")


def _run_tests(clone: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
        cwd=clone,
        capture_output=True,
        text=True,
    )


def verify() -> None:
    """Clone the demo, check each branch's tests, then merge both features.

    The scratch branch only exists in the temp clone. ``demo/api-demo/`` keeps
    exactly ``main``, ``feat/cents-payload``, ``feat/fee-on-cents``, and
    ``docs/readme``.
    """
    with tempfile.TemporaryDirectory() as tmp:
        clone = Path(tmp) / "clone"
        subprocess.run(
            ["git", "clone", "-q", "--no-single-branch", str(DEMO_DIR), str(clone)],
            check=True,
        )
        for branch in ("main", "feat/cents-payload", "feat/fee-on-cents", "docs/readme"):
            git(clone, "checkout", "-q", "--detach", f"origin/{branch}")
            proc = _run_tests(clone)
            if proc.returncode != 0:
                print(proc.stdout)
                print(proc.stderr)
                raise SystemExit(f"UNEXPECTED: tests failed on {branch} alone.")
            print(f"{branch}: tests passed")

        git(clone, "checkout", "-q", "-b", "scratch/merge-both", "origin/main")
        git(clone, "merge", "-q", "--no-edit", "origin/feat/cents-payload")
        git(clone, "merge", "-q", "--no-edit", "origin/feat/fee-on-cents")
        print("Merged feat/cents-payload and feat/fee-on-cents into scratch/merge-both")
        print("Running tests on the merged result...")
        proc = _run_tests(clone)
        print(proc.stdout)
        if proc.stderr:
            print(proc.stderr)
        if proc.returncode == 0:
            print("UNEXPECTED: all tests passed after the merge.")
            raise SystemExit(1)
        print("Expected failure: the merged result divides by 100 twice.")


if __name__ == "__main__":
    create_repo()
    if "--verify" in sys.argv:
        verify()
