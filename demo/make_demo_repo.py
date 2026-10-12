#!/usr/bin/env python3
"""Create the MergeGuard demo repo with a semantic conflict git cannot see.

The demo repo lives in ``demo/checkout-demo/`` (git-ignored). It is recreated
from scratch on every run:

- ``main``: a tiny ``checkout.py`` (price x qty, discount, tax) plus
  ``python3 -m unittest`` tests.
- ``feat/discount-cap``: caps the discount at a fixed amount, applied to the
  pre-tax subtotal. Touches only ``apply_discount`` (top of the file).
- ``feat/tax-before-discount``: applies tax *before* the discount. Touches
  only ``total`` (bottom of the file).
- ``docs/readme``: harmless branch that only edits ``README.md``.

Both feature branches change the SAME file but in far-apart regions (more
than the 3-line adjacency window), so the git heuristic reports a low risk
(1 shared file, 0 overlapping) while Nemotron sees both changes and flags the
semantic clash.

Each branch passes its tests on its own. After merging both branches (no
textual conflict) the result is wrong and at least one test fails.

Usage:
    python3 demo/make_demo_repo.py            # (re)create the demo repo
    python3 demo/make_demo_repo.py --verify   # also verify the merge failure
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DEMO_DIR = Path(__file__).resolve().parent / "checkout-demo"
GIT_NAME = "MergeGuard Demo"
GIT_EMAIL = "demo@mergeguard.local"

# ---------------------------------------------------------------------------
# main branch
# ---------------------------------------------------------------------------

CHECKOUT_MAIN = '''"""Tiny checkout module: price x qty, discount, tax."""


def line_total(price: float, qty: int) -> float:
    """Subtotal for a single line item."""
    return round(price * qty, 2)


def apply_discount(subtotal: float, discount_pct: float) -> float:
    """Apply a percentage discount to a subtotal."""
    if discount_pct <= 0:
        return subtotal
    return round(subtotal * (1 - discount_pct / 100.0), 2)


def apply_tax(amount: float, tax_pct: float) -> float:
    """Apply a percentage tax to an amount."""
    if tax_pct <= 0:
        return amount
    return round(amount * (1 + tax_pct / 100.0), 2)


def total(price: float, qty: int, discount_pct: float, tax_pct: float) -> float:
    """Full order total: discount first, then tax on the discounted amount."""
    subtotal = line_total(price, qty)
    discounted = apply_discount(subtotal, discount_pct)
    return apply_tax(discounted, tax_pct)
'''

TESTS_MAIN = '''import unittest

import checkout


class CheckoutTests(unittest.TestCase):
    def test_line_total(self):
        self.assertEqual(checkout.line_total(10.0, 3), 30.0)

    def test_discount(self):
        self.assertEqual(checkout.apply_discount(100.0, 10.0), 90.0)

    def test_tax(self):
        self.assertEqual(checkout.apply_tax(100.0, 10.0), 110.0)

    def test_total_discount_then_tax(self):
        # 100 subtotal, 10% discount -> 90, then 10% tax -> 99.0
        self.assertEqual(checkout.total(10.0, 10, 10.0, 10.0), 99.0)


if __name__ == "__main__":
    unittest.main()
'''

README_MAIN = """# Checkout Demo

A tiny checkout module used by the MergeGuard demo.

Run the tests with:

    python3 -m unittest discover -s tests
"""

# ---------------------------------------------------------------------------
# feat/discount-cap: cap the discount at a fixed amount, pre-tax subtotal
# ---------------------------------------------------------------------------

CHECKOUT_DISCOUNT_CAP = '''"""Tiny checkout module: price x qty, discount, tax."""

# ASSUMPTION: MAX_DISCOUNT is a cap on the PRE-TAX subtotal.
MAX_DISCOUNT = 20.00


def line_total(price: float, qty: int) -> float:
    """Subtotal for a single line item."""
    return round(price * qty, 2)


def apply_discount(subtotal: float, discount_pct: float) -> float:
    """Apply a percentage discount, capped at MAX_DISCOUNT.

    ASSUMPTION: subtotal is the PRE-TAX amount; the cap is pre-tax.
    """
    if discount_pct <= 0:
        return subtotal
    discount = min(subtotal * discount_pct / 100.0, MAX_DISCOUNT)
    return round(subtotal - discount, 2)


def apply_tax(amount: float, tax_pct: float) -> float:
    """Apply a percentage tax to an amount."""
    if tax_pct <= 0:
        return amount
    return round(amount * (1 + tax_pct / 100.0), 2)


def total(price: float, qty: int, discount_pct: float, tax_pct: float) -> float:
    """Full order total: discount first, then tax on the discounted amount."""
    subtotal = line_total(price, qty)
    discounted = apply_discount(subtotal, discount_pct)
    return apply_tax(discounted, tax_pct)
'''

TEST_DISCOUNT_CAP = '''import unittest

import checkout


class DiscountCapTests(unittest.TestCase):
    def test_discount_capped_at_max(self):
        # 200 subtotal, 50% discount would be 100, but the cap is 20.
        self.assertEqual(checkout.apply_discount(200.0, 50.0), 180.0)

    def test_small_discount_unchanged(self):
        # 100 subtotal, 10% discount is 10, below the cap.
        self.assertEqual(checkout.apply_discount(100.0, 10.0), 90.0)

    def test_total_with_cap(self):
        # 200 subtotal, 50% discount capped to 20 -> 180, then 10% tax -> 198.
        self.assertEqual(checkout.total(20.0, 10, 50.0, 10.0), 198.0)


if __name__ == "__main__":
    unittest.main()
'''

# ---------------------------------------------------------------------------
# feat/tax-before-discount: tax applied before the discount
# ---------------------------------------------------------------------------

CHECKOUT_TAX_FIRST = '''"""Tiny checkout module: price x qty, discount, tax."""


def line_total(price: float, qty: int) -> float:
    """Subtotal for a single line item."""
    return round(price * qty, 2)


def apply_discount(subtotal: float, discount_pct: float) -> float:
    """Apply a percentage discount to a subtotal."""
    if discount_pct <= 0:
        return subtotal
    return round(subtotal * (1 - discount_pct / 100.0), 2)


def apply_tax(amount: float, tax_pct: float) -> float:
    """Apply a percentage tax to an amount."""
    if tax_pct <= 0:
        return amount
    return round(amount * (1 + tax_pct / 100.0), 2)


def total(price: float, qty: int, discount_pct: float, tax_pct: float) -> float:
    """Full order total: tax first, then discount on the taxed amount.

    CHANGE: tax is now applied BEFORE the discount, so apply_discount
    receives the TAX-INCLUSIVE amount. Any discount cap or percentage that
    was tuned for pre-tax amounts now applies to a larger base.
    """
    subtotal = line_total(price, qty)
    taxed = apply_tax(subtotal, tax_pct)
    return apply_discount(taxed, discount_pct)
'''

TEST_TAX_ORDER = '''import unittest

import checkout


class TaxOrderTests(unittest.TestCase):
    def test_tax_applied_before_discount(self):
        # 100 subtotal, 10% tax -> 110, then 10% discount -> 99.0
        self.assertEqual(checkout.total(10.0, 10, 10.0, 10.0), 99.0)

    def test_tax_then_discount_differs_from_discount_then_tax(self):
        # 100 subtotal: tax first (110) then 10% discount -> 99.0
        # discount first (90) then 10% tax -> 99.0 as well for these numbers,
        # so use a case where the order matters: 100, 20% tax, 10% discount.
        # tax first: 120 -> 108.0 ; discount first: 90 -> 108.0.
        # Use 100, 10% tax, 20% discount: tax first 110 -> 88.0.
        self.assertEqual(checkout.total(10.0, 10, 20.0, 10.0), 88.0)


if __name__ == "__main__":
    unittest.main()
'''

# ---------------------------------------------------------------------------
# docs/readme: harmless
# ---------------------------------------------------------------------------

README_DOCS = """# Checkout Demo

A tiny checkout module used by the MergeGuard demo.

Run the tests with:

    python3 -m unittest discover -s tests

## Notes

- The discount is applied to the pre-tax subtotal.
- Tax is applied after the discount.
"""


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), "-c", f"user.name={GIT_NAME}",
         "-c", f"user.email={GIT_EMAIL}", *args],
        capture_output=True,
        text=True,
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
    write(DEMO_DIR / "checkout.py", CHECKOUT_MAIN)
    write(DEMO_DIR / "tests" / "test_checkout.py", TESTS_MAIN)
    write(DEMO_DIR / "README.md", README_MAIN)
    git(DEMO_DIR, "add", "-A")
    git(DEMO_DIR, "commit", "-q", "-m", "Add checkout module with tests")

    # feat/discount-cap
    git(DEMO_DIR, "checkout", "-q", "-b", "feat/discount-cap")
    write(DEMO_DIR / "checkout.py", CHECKOUT_DISCOUNT_CAP)
    write(DEMO_DIR / "tests" / "test_discount_cap.py", TEST_DISCOUNT_CAP)
    git(DEMO_DIR, "add", "-A")
    git(DEMO_DIR, "commit", "-q", "-m",
        "Cap the discount at MAX_DISCOUNT on the pre-tax subtotal\n\n"
        "ASSUMPTION: apply_discount receives the PRE-TAX subtotal; the cap\n"
        "is defined on pre-tax amounts.")

    # feat/tax-before-discount
    git(DEMO_DIR, "checkout", "-q", "main")
    git(DEMO_DIR, "checkout", "-q", "-b", "feat/tax-before-discount")
    write(DEMO_DIR / "checkout.py", CHECKOUT_TAX_FIRST)
    write(DEMO_DIR / "tests" / "test_tax_order.py", TEST_TAX_ORDER)
    git(DEMO_DIR, "add", "-A")
    git(DEMO_DIR, "commit", "-q", "-m",
        "Apply tax before the discount in total()\n\n"
        "CHANGE: tax is now applied BEFORE the discount, so apply_discount\n"
        "receives the TAX-INCLUSIVE amount.")

    # docs/readme
    git(DEMO_DIR, "checkout", "-q", "main")
    git(DEMO_DIR, "checkout", "-q", "-b", "docs/readme")
    write(DEMO_DIR / "README.md", README_DOCS)
    git(DEMO_DIR, "add", "-A")
    git(DEMO_DIR, "commit", "-q", "-m", "Document discount and tax order")

    git(DEMO_DIR, "checkout", "-q", "main")
    print(f"Demo repo created at {DEMO_DIR}")
    print("Branches: main, feat/discount-cap, feat/tax-before-discount, docs/readme")


def verify() -> None:
    """Clone the demo repo to a temp dir, merge both feature branches into a
    scratch branch, run the tests, and print the failing assert."""
    with tempfile.TemporaryDirectory() as tmp:
        clone = Path(tmp) / "clone"
        subprocess.run(
            ["git", "clone", "-q", "--no-single-branch", str(DEMO_DIR), str(clone)],
            check=True,
        )
        git(clone, "checkout", "-q", "-b", "scratch/merge-both")
        # The clone tracks the demo repo's branches as origin/* refs.
        git(clone, "merge", "-q", "--no-edit", "origin/feat/discount-cap")
        git(clone, "merge", "-q", "--no-edit", "origin/feat/tax-before-discount")
        print("Merged feat/discount-cap and feat/tax-before-discount into scratch/merge-both")
        print("Running tests on the merged result...")
        proc = subprocess.run(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
            cwd=clone,
            capture_output=True,
            text=True,
        )
        print(proc.stdout)
        if proc.stderr:
            print(proc.stderr)
        if proc.returncode == 0:
            print("UNEXPECTED: all tests passed after the merge.")
        else:
            print("Expected failure: the merged result is semantically wrong.")


if __name__ == "__main__":
    create_repo()
    if "--verify" in sys.argv:
        verify()
