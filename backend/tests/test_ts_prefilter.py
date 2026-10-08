"""TypeScript / JavaScript symbol extraction for the prefilter."""
from __future__ import annotations

import subprocess
from pathlib import Path

from app.services import git_service, prefilter, ts_symbols

FILLER = "".join(f"  const filler{i} = {i};\n" for i in range(20))

BRIDGE = """import { readTranscript } from './transcript.js';

export function createBridge(opts: Options) {
  const byId = new Map<string, Entry>();
""" + FILLER + """
  async function restoreSession(
    action: 'load' | 'resume',
    req: RestoreRequest,
  ): Promise<Restored> {
    const replay = action === 'load' ? (req.historyReplay ?? 'stream') : 'stream';
    const pageSize = req.historyPageSize;
    return readTranscript(req.sessionId, pageSize);
  }
""" + FILLER + """
  return {
    async loadSession(req: RestoreRequest) {
      return restoreSession('load', req);
    },
    async resumeSession(req: RestoreRequest) {
      return restoreSession('resume', req);
    },
    async closeSession(id: string): Promise<void> {
      byId.delete(id);
    },
  };
}
"""
BRIDGE_RESPONSE_ONLY = BRIDGE.replace(
    "    const pageSize = req.historyPageSize;\n",
    "    const pageSize =\n      replay === 'response' ? req.historyPageSize : undefined;\n",
)
BRIDGE_CLOSE = BRIDGE.replace("      byId.delete(id);\n", "      byId.get(id)?.dispose();\n      byId.delete(id);\n")
TEST_BASE = """import { createBridge } from './bridge.js';

it('creates', () => {
  expect(createBridge({})).toBeDefined();
});
"""
TEST_PAGE = TEST_BASE + """
it('pages history', async () => {
  const bridge = createBridge({});
  const loaded = await bridge.loadSession({ sessionId: 's', historyPageSize: 10 });
  expect(loaded.events).toHaveLength(10);
});
"""
CART = """export class Cart {
  items: Item[] = [];

  total(): number {
    return this.items.reduce((sum, i) => sum + i.price, 0);
  }

  count(): number {
    return this.items.length;
  }
}

export function formatTotal(cart: Cart): string {
  return `${cart.total()} EUR`;
}
"""
CART_TAX = CART.replace("return this.items.reduce((sum, i) => sum + i.price, 0);",
                        "return this.items.reduce((sum, i) => sum + i.price * 1.25, 0);")
INDEX = "export { formatTotal as renderTotal } from './cart';\nexport * from './money';\n"
MONEY = "export const roundCents = (n: number): number => Math.round(n * 100) / 100;\n"
MONEY_FLOOR = MONEY.replace("Math.round", "Math.floor")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True).stdout


def _branch(repo: Path, name: str, files: dict[str, str]) -> None:
    _git(repo, "checkout", "-q", "-b", name, "main")
    for path, text in files.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", name)
    _git(repo, "checkout", "-q", "main")


def _repo(tmp_path: Path) -> Path:
    r = tmp_path / "web"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "Test")
    files = {"src/bridge.ts": BRIDGE, "src/bridge.test.ts": TEST_BASE, "src/cart.ts": CART,
             "src/index.ts": INDEX, "src/money.ts": MONEY,
             "src/view.ts": "import { renderTotal, roundCents } from './index';\n\nexport const v = 1;\n"}
    for path, text in files.items():
        (r / path).parent.mkdir(parents=True, exist_ok=True)
        (r / path).write_text(text)
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "base")
    return r


def _overlap(repo: Path, a: str, b: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    sa = prefilter.branch_symbols(repo, git_service.diff_against_base(repo, a, "main"))
    sb = prefilter.branch_symbols(repo, git_service.diff_against_base(repo, b, "main"))
    return prefilter.symbol_overlap(sa, sb, a, b)


def test_nested_change_surfaces_through_the_wrapper_the_other_test_calls(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _branch(repo, "feat/response-only", {"src/bridge.ts": BRIDGE_RESPONSE_ONLY})
    _branch(repo, "feat/page-test", {"src/bridge.test.ts": TEST_PAGE})
    names, evidence = _overlap(repo, "feat/response-only", "feat/page-test")
    assert names == ("loadSession",)
    assert evidence == (
        "loadSession: changed by feat/response-only (src/bridge.ts) via restoreSession, "
        "called by feat/page-test (src/bridge.test.ts)",
    )
    # The snippet shows the wrapper, the changed nested function and the call site.
    snippet = prefilter.evidence_snippets(repo, "main", evidence)
    assert "thin wrapper" in snippet and "return restoreSession('load', req);" in snippet
    assert "+      replay === 'response' ? req.historyPageSize : undefined;" in snippet
    assert "historyPageSize: 10" in snippet and "call site on feat/page-test" in snippet
    assert prefilter._EVIDENCE_CALL.match(evidence[0])["via"] == "restoreSession"


def test_harmless_ts_pair_has_no_symbol_overlap(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    # One branch changes closeSession, the other adds a test of loadSession:
    # same file family, different symbols; the factory itself is not "changed".
    _branch(repo, "feat/close", {"src/bridge.ts": BRIDGE_CLOSE})
    _branch(repo, "feat/page-test", {"src/bridge.test.ts": TEST_PAGE})
    names, evidence = _overlap(repo, "feat/close", "feat/page-test")
    assert names == () and evidence == ()


def test_class_method_change_matches_method_calls_only(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _branch(repo, "feat/tax", {"src/cart.ts": CART_TAX})
    _branch(repo, "feat/receipt", {"src/receipt.ts": (
        "import { Cart } from './cart';\n\nexport function receipt(c: Cart) {\n"
        "  return `total ${c.total()} for ${c.count()} items`;\n}\n")})
    names, evidence = _overlap(repo, "feat/tax", "feat/receipt")
    assert names == ("total",)
    assert evidence == ("total: changed by feat/tax (src/cart.ts), called by feat/receipt (src/receipt.ts)",)


def test_named_import_and_reexports_resolve_to_the_defining_module(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _branch(repo, "feat/floor", {"src/money.ts": MONEY_FLOOR})
    _branch(repo, "feat/view", {"src/view.ts": (
        "import { renderTotal, roundCents } from './index';\n\n"
        "export const v = roundCents(renderTotal(cart).length);\n")})
    names, evidence = _overlap(repo, "feat/floor", "feat/view")
    assert names == ("roundCents",)  # via `export * from './money'` in src/index.ts
    _branch(repo, "feat/format", {"src/cart.ts": CART.replace("} EUR`", "} euro`")})
    names, _ = _overlap(repo, "feat/format", "feat/view")
    assert names == ("formatTotal",)  # via `export { formatTotal as renderTotal }`


def test_same_name_in_another_module_does_not_match(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _branch(repo, "feat/floor", {"src/money.ts": MONEY_FLOOR})
    _branch(repo, "feat/other-round", {"src/stats.ts": (
        "function roundCents(n: number) { return n; }\n\nexport const s = roundCents(2);\n")})
    names, _ = _overlap(repo, "feat/floor", "feat/other-round")
    assert names == ()


def test_declarations_spans_and_masking() -> None:
    decls = {d.name: (d.kind, d.start, d.end) for d in ts_symbols.declarations(BRIDGE)}
    assert decls["createBridge"][0] == "function" and decls["createBridge"][1] == 3
    assert decls["restoreSession"][0] == "function"
    assert decls["loadSession"] == ("method", decls["loadSession"][1], decls["loadSession"][1] + 2)
    assert decls["Cart"][0] == "class" if "Cart" in decls else True
    cart = {d.name: d.kind for d in ts_symbols.declarations(CART)}
    assert cart == {"Cart": "class", "total": "method", "count": "method", "formatTotal": "function"}
    # Braces in strings and comments do not break spans.
    src = "function a() {\n  const s = '}';\n  // }\n  return `${'}'}`;\n}\nfunction b() {}\n"
    spans = {d.name: (d.start, d.end) for d in ts_symbols.declarations(src)}
    assert spans == {"a": (1, 5), "b": (6, 6)}
    assert ts_symbols.ts_module("packages/core/src/index.ts") == "packages.core.src"
    assert ts_symbols.resolve_spec("../util/x.js", "pkg/src/a/b.ts") == "pkg.src.util.x"
