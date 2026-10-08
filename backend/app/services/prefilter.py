"""Cheap static prefilter that runs before any LLM call.

For each branch we extract, from the files it changed:

- ``defs``: functions / classes / module constants whose code the branch
  touched, qualified by the module that defines them;
- ``calls``: functions the branch calls from code it touched, resolved to a
  module where the imports allow it.

A symbol overlap means one branch CHANGED a definition that the other branch
also changed or calls (PR A edits ``def f``, PR B calls ``f``). Calls that both
branches make to a function neither of them changed never count.

Python files are parsed with :mod:`ast` and calls are resolved through the
file's imports, so ``checkout.total(...)`` only matches a ``total`` defined in
``checkout.py``, a bare ``total(...)`` only matches a ``total`` imported from or
defined in its own module, and ``obj.total()`` (a method call on some object)
only matches a changed *method* named ``total``. Other languages use a regex
heuristic (``def`` / ``function`` / ``func`` / ``fn`` names and ``name(`` call
sites on changed lines) matched by name.

A class counts as changed when its header or one of its field declarations
changed (the fields are the constructor of a dataclass / pydantic model), and
calls are followed through re-exports (``from pkg.state import Model`` where
``pkg/state.py`` re-imports ``Model`` from the module that defines it).

A pair is a model candidate only when the branches share a file or have a
symbol overlap. Candidates are ranked and only the top K reach Nemotron.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.schemas import BranchDiff
from app.services import git_service

# Dunders, builtins and unittest helpers never count as an overlap.
_IGNORED_NAMES = {
    "__init__", "__repr__", "__str__", "__eq__", "__hash__", "main", "setUp",
    "tearDown", "print", "len", "str", "int", "float", "dict", "list", "set",
    "tuple", "range", "round", "min", "max", "isinstance", "super", "open",
}
_KEYWORDS = {
    "if", "for", "while", "switch", "catch", "return", "function", "def", "func",
    "fn", "elif", "with", "await", "new", "typeof", "sizeof", "not", "and", "or",
    "assert", "print", "lambda", "yield", "in", "is", "else", "try", "except",
}
_REGEX_DEF = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?"
    r"(?:def|function\*?|func|fn|sub)\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)"
)
_REGEX_ARROW = re.compile(
    r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_]\w*)\s*=\s*(?:async\s*)?"
    r"(?:\([^)]*\)|[A-Za-z_]\w*)\s*=>"
)
_REGEX_CALL = re.compile(r"\b([A-Za-z_]\w*)\s*\(")
MAX_FILE_BYTES = 400_000

# Call targets that are not a resolved module.
ANY = "*"  # regex heuristic: match a changed def of that name in any module
METHOD = "."  # obj.name(): match a changed method of that name
UNRESOLVED = "?"  # bare name that is neither imported nor defined here


@dataclass(frozen=True)
class Def:
    module: str
    name: str
    kind: str  # "function" | "method" | "class" | "constant"
    path: str


@dataclass(frozen=True)
class Call:
    module: str  # dotted module, or ANY / METHOD / UNRESOLVED
    name: str
    path: str


@dataclass
class BranchSymbols:
    defs: set[Def] = field(default_factory=set)
    calls: set[Call] = field(default_factory=set)

    @property
    def def_names(self) -> set[str]:
        return {d.name for d in self.defs}

    @property
    def call_names(self) -> set[str]:
        return {c.name for c in self.calls}


@dataclass(frozen=True)
class PairSignal:
    shared_files: int
    overlapping_files: int
    symbols: tuple[str, ...]
    evidence: tuple[str, ...] = ()

    @property
    def is_candidate(self) -> bool:
        return self.shared_files > 0 or bool(self.symbols)


def module_name(path: str) -> str:
    """``pkg/mod.py`` -> ``pkg.mod``; ``pkg/__init__.py`` -> ``pkg``."""
    parts = list(Path(path).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _same_module(a: str, b: str) -> bool:
    """Dotted names refer to the same module if one is a suffix of the other
    on component boundaries (``app.services.x`` vs ``backend.app.services.x``)."""
    if not a or not b:
        return False
    pa, pb = a.strip(".").split("."), b.strip(".").split(".")
    n = min(len(pa), len(pb))
    return pa[-n:] == pb[-n:]


def _in_hunks(start: int, end: int, hunks: list[tuple[int, int]]) -> bool:
    return any(start <= h_end and h_start <= end for h_start, h_end in hunks)


class _Imports:
    def __init__(self, tree: ast.Module) -> None:
        self.modules: dict[str, str] = {}  # alias -> module
        self.names: dict[str, tuple[str, str]] = {}  # local name -> (module, name)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.asname:
                        self.modules[alias.asname] = alias.name
                    else:
                        self.modules[alias.name.split(".")[0]] = alias.name.split(".")[0]
                        self.modules[alias.name] = alias.name
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                for alias in node.names:
                    local = alias.asname or alias.name
                    if alias.name == "*":
                        continue
                    # `from pkg import mod` may import a module or a name.
                    self.modules.setdefault(local, f"{base}.{alias.name}".strip("."))
                    self.names[local] = (base, alias.name)


def _dotted(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        inner = _dotted(node.value)
        return f"{inner}.{node.attr}" if inner else None
    return None


def _resolve_call(
    node: ast.Call, imports: _Imports, local_defs: set[str], this_module: str, path: str
) -> list[Call]:
    func = node.func
    if isinstance(func, ast.Name):
        if func.id in imports.names:
            module, name = imports.names[func.id]
            return [Call(module, name, path)]
        if func.id in local_defs:
            return [Call(this_module, func.id, path)]
        return [Call(UNRESOLVED, func.id, path)]
    if isinstance(func, ast.Attribute):
        base = _dotted(func.value)
        if base and base in imports.modules:
            return [Call(imports.modules[base], func.attr, path)]
        if base and base.split(".")[0] in imports.modules:
            return [Call(base, func.attr, path)]
        return [Call(METHOD, func.attr, path)]
    return []


def python_symbols(
    source: str, hunks: list[tuple[int, int]], path: str = "module.py"
) -> BranchSymbols | None:
    """Changed defs and the calls made from changed code. ``None`` if unparsable."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return None
    this_module = module_name(path)
    imports = _Imports(tree)
    local_defs = {
        n.name
        for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    out = BranchSymbols()
    changed_funcs: list[ast.AST] = []

    def visit(body: list[ast.stmt], in_class: bool) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                end = getattr(node, "end_lineno", None) or node.lineno
                start = min([node.lineno] + [d.lineno for d in node.decorator_list])
                if isinstance(node, ast.ClassDef):
                    # A class changes when its header changes or when one of its
                    # field declarations does: for dataclasses, pydantic models,
                    # attrs and NamedTuples the fields ARE the constructor.
                    if _in_hunks(start, node.lineno, hunks) or any(
                        _in_hunks(
                            stmt.lineno, getattr(stmt, "end_lineno", None) or stmt.lineno, hunks
                        )
                        for stmt in node.body
                        if isinstance(stmt, (ast.AnnAssign, ast.Assign))
                    ):
                        out.defs.add(Def(this_module, node.name, "class", path))
                    visit(node.body, in_class=True)
                elif _in_hunks(start, end, hunks):
                    kind = "method" if in_class else "function"
                    out.defs.add(Def(this_module, node.name, kind, path))
                    changed_funcs.append(node)

    visit(tree.body, in_class=False)

    def add_call(call: ast.Call) -> None:
        for resolved in _resolve_call(call, imports, local_defs, this_module, path):
            out.calls.add(resolved)

    # Calls made from inside changed functions, plus calls on changed lines.
    for node in changed_funcs:
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                add_call(sub)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            end = getattr(node, "end_lineno", None) or node.lineno
            if _in_hunks(node.lineno, end, hunks):
                add_call(node)
    # Changed module-level constants / assignments (e.g. MAX_DISCOUNT = 20).
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            end = getattr(node, "end_lineno", None) or node.lineno
            if _in_hunks(node.lineno, end, hunks):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name):
                        out.defs.add(Def(this_module, target.id, "constant", path))
    return out


def regex_symbols(
    source: str, hunks: list[tuple[int, int]], path: str = "file"
) -> BranchSymbols:
    """Language-agnostic heuristic: def/function names and call sites."""
    out = BranchSymbols()
    this_module = module_name(path)
    lines = source.splitlines()
    def_lines: list[tuple[int, str]] = []
    for index, line in enumerate(lines, start=1):
        match = _REGEX_DEF.match(line) or _REGEX_ARROW.match(line)
        if match:
            def_lines.append((index, match.group(1)))
    names: set[str] = set()
    calls: set[str] = set()
    for start, end in hunks:
        # Defs declared on a changed line, or the nearest def above the hunk.
        enclosing = [name for line_no, name in def_lines if line_no <= start]
        if enclosing:
            names.add(enclosing[-1])
        for line_no, name in def_lines:
            if start <= line_no <= end:
                names.add(name)
        for line in lines[max(start - 1, 0) : end]:
            calls.update(_REGEX_CALL.findall(line))
    out.defs = {Def(this_module, n, "function", path) for n in names - _KEYWORDS}
    out.calls = {Call(ANY, n, path) for n in calls - _KEYWORDS}
    return out


def file_symbols(path: str, source: str, hunks: list[tuple[int, int]]) -> BranchSymbols:
    if path.endswith(".py"):
        parsed = python_symbols(source, hunks, path)
        if parsed is not None:
            return parsed
    return regex_symbols(source, hunks, path)


def _reexports(source: str, this_module: str, is_package: bool) -> dict[str, str]:
    """``name -> source module`` for names a module imports with ``from X import``.

    Used to follow re-exports: ``from pkg.state import Model`` where
    ``pkg/state.py`` itself does ``from core.models import Model``.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return {}
    package = this_module.split(".") if is_package else this_module.split(".")[:-1]
    out: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level:
            keep = len(package) - (node.level - 1)
            if keep < 0:
                continue
            base = ".".join(package[:keep] + ([node.module] if node.module else []))
        else:
            base = node.module or ""
        for alias in node.names:
            if alias.name != "*" and base:
                out[alias.asname or alias.name] = f"{base}:{alias.name}"
    return out


class _ModuleIndex:
    """Find a branch's Python file for a dotted module name (suffix match)."""

    def __init__(self, repo: Path, ref: str) -> None:
        self.repo = repo
        self.ref = ref
        self._by_module: dict[str, list[str]] | None = None
        self._exports: dict[str, dict[str, str]] = {}

    def _index(self) -> dict[str, list[str]]:
        if self._by_module is None:
            self._by_module = {}
            try:
                listing = git_service._run(self.repo, "ls-tree", "-r", "--name-only", self.ref)
            except git_service.GitError:
                listing = ""
            for path in listing.splitlines():
                if path.endswith(".py"):
                    self._by_module.setdefault(module_name(path), []).append(path)
        return self._by_module

    def paths(self, module: str) -> list[str]:
        want = module.strip(".").split(".")
        found = []
        for mod, paths in self._index().items():
            parts = mod.split(".")
            if len(parts) >= len(want) and parts[-len(want):] == want:
                found.extend(paths)
        return sorted(found)[:3]

    def exports(self, path: str) -> dict[str, str]:
        if path not in self._exports:
            try:
                source = git_service.show_file(self.repo, self.ref, path) or ""
            except git_service.GitError:
                source = ""
            self._exports[path] = _reexports(
                source, module_name(path), path.endswith("__init__.py")
            )
        return self._exports[path]


REEXPORT_DEPTH = 3


def resolve_reexports(calls: set[Call], index: _ModuleIndex) -> set[Call]:
    """Add the defining module for calls that go through a re-export."""
    extra: set[Call] = set()
    for call in calls:
        if call.module in (ANY, METHOD, UNRESOLVED):
            continue
        module, name = call.module, call.name
        for _ in range(REEXPORT_DEPTH):
            target = None
            for path in index.paths(module):
                target = index.exports(path).get(name)
                if target:
                    break
            if not target:
                break
            module, name = target.split(":", 1)
            extra.add(Call(module, name, call.path))
    return calls | extra


def branch_symbols(repo: Path, diff: BranchDiff) -> BranchSymbols:
    """Union of changed symbols across a branch's changed files."""
    total = BranchSymbols()
    for changed in diff.files:
        if not changed.hunks:
            continue
        try:
            source = git_service.show_file(repo, diff.branch, changed.path)
        except git_service.GitError:
            continue
        if source is None or len(source) > MAX_FILE_BYTES:
            continue
        syms = file_symbols(changed.path, source, changed.hunks)
        total.defs |= syms.defs
        total.calls |= syms.calls
    total.defs = {d for d in total.defs if d.name not in _IGNORED_NAMES}
    total.calls = {c for c in total.calls if c.name not in _IGNORED_NAMES}
    if total.calls:
        total.calls = resolve_reexports(total.calls, _ModuleIndex(repo, diff.branch))
    return total


def _call_hits(call: Call, d: Def) -> bool:
    if call.name != d.name:
        return False
    if call.module == ANY:
        return True
    if call.module == METHOD:
        return d.kind == "method"
    if call.module == UNRESOLVED:
        return False  # a builtin, a local, or a star import: not evidence
    return _same_module(call.module, d.module)


def symbol_overlap(
    a: BranchSymbols,
    b: BranchSymbols,
    name_a: str = "A",
    name_b: str = "B",
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(symbol names, human-readable evidence) for a branch pair.

    Counts only (1) a definition both branches changed, or (2) a definition
    one branch changed that the other calls.
    """
    hits: dict[str, set[str]] = {}

    for da in a.defs:
        for db in b.defs:
            if da.name == db.name and da.kind == db.kind and (
                da.path == db.path or _same_module(da.module, db.module)
            ):
                hits.setdefault(da.name, set()).add(
                    f"{da.name}: changed by both {name_a} and {name_b} ({da.path})"
                )
    for defs, calls, changer, caller in ((a.defs, b.calls, name_a, name_b),
                                         (b.defs, a.calls, name_b, name_a)):
        for d in defs:
            for c in calls:
                if _call_hits(c, d):
                    hits.setdefault(d.name, set()).add(
                        f"{d.name}: changed by {changer} ({d.path}), called by {caller} ({c.path})"
                    )
    names = tuple(sorted(hits))
    evidence = tuple(sorted(e for name in names for e in hits[name]))
    return names, evidence


# ---------------------------------------------------------------------------
# Evidence snippets for the model: the changed definition and the call site
# ---------------------------------------------------------------------------

_EVIDENCE_CALL = re.compile(
    r"^(?P<name>[\w.]+): changed by (?P<changer>\S+) \((?P<def_path>[^()]+)\), "
    r"called by (?P<caller>\S+) \((?P<call_path>[^()]+)\)$"
)
MAX_SNIPPET_LINES = 30


def _find_def(tree: ast.AST, name: str) -> ast.AST | None:
    for node in ast.walk(tree):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == name:
                return node
    return None


def _params(node: ast.AST | None) -> dict[str, bool]:
    """What callers depend on: ``name -> required`` for a function's parameters
    or a class's fields (dataclass / pydantic style) and ``__init__``."""
    if node is None:
        return {}
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        a = node.args
        positional = [*a.posonlyargs, *a.args]
        n_required = len(positional) - len(a.defaults)
        out = {arg.arg: i < n_required for i, arg in enumerate(positional)}
        for arg, default in zip(a.kwonlyargs, a.kw_defaults):
            out[arg.arg] = default is None
        out.pop("self", None)
        out.pop("cls", None)
        return out
    if isinstance(node, ast.ClassDef):
        out = {}
        for stmt in node.body:
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                if not stmt.target.id.startswith("_") and stmt.target.id != "model_config":
                    out[stmt.target.id] = stmt.value is None
        init = next(
            (n for n in node.body if isinstance(n, ast.FunctionDef) and n.name == "__init__"), None
        )
        out.update(_params(init))
        return out
    return {}


def signature_change(old: ast.AST | None, new: ast.AST | None) -> str | None:
    """Describe a caller-visible change between two versions of a def, if any."""
    if old is None or new is None:
        return None
    before, after = _params(old), _params(new)
    added_required = [k for k, req in after.items() if req and not before.get(k, False)]
    removed = [k for k in before if k not in after]
    added_optional = [k for k, req in after.items() if not req and k not in before]
    if added_required or removed:
        parts = []
        if added_required:
            parts.append("new required " + ", ".join(added_required))
        if removed:
            parts.append("removed " + ", ".join(removed))
        return "breaking signature change: " + "; ".join(parts)
    if added_optional:
        return "compatible signature change: new optional " + ", ".join(added_optional)
    return None


def _parse(source: str | None) -> ast.AST | None:
    if not source:
        return None
    try:
        return ast.parse(source)
    except (SyntaxError, ValueError):
        return None


def _lines(source: str, node: ast.AST) -> list[str]:
    start = min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])])
    end = getattr(node, "end_lineno", None) or node.lineno
    return source.splitlines()[start - 1 : end]


def _first_call(tree: ast.AST, name: str) -> ast.Call | None:
    calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and (
            (isinstance(n.func, ast.Name) and n.func.id == name)
            or (isinstance(n.func, ast.Attribute) and n.func.attr == name)
        )
    ]
    return min(calls, key=lambda n: n.lineno) if calls else None


def call_mismatch(call: ast.Call, old: ast.AST | None, new: ast.AST | None) -> str | None:
    """Does this call site break against the new signature? Only for calls
    whose arguments are all keywords (no ``*args`` / ``**kwargs``)."""
    if old is None or new is None or call.args:
        return None
    if any(k.arg is None for k in call.keywords):
        return None
    before, after = _params(old), _params(new)
    passed = {k.arg for k in call.keywords}
    missing = [k for k, req in after.items() if req and not before.get(k, False) and k not in passed]
    stale = [k for k in before if k not in after and k in passed]
    parts = []
    if missing:
        parts.append("call site does not pass new required " + ", ".join(missing))
    if stale:
        parts.append("call site still passes removed " + ", ".join(stale))
    return "; ".join(parts) or None


def _call_site(source: str, tree: ast.AST, name: str, around: int = 3) -> str | None:
    calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and (
            (isinstance(n.func, ast.Name) and n.func.id == name)
            or (isinstance(n.func, ast.Attribute) and n.func.attr == name)
        )
    ]
    if not calls:
        return None
    call = min(calls, key=lambda n: n.lineno)
    lines = source.splitlines()
    start = max(call.lineno - around, 1)
    end = min((getattr(call, "end_lineno", None) or call.lineno) + around, len(lines))
    end = min(end, start + MAX_SNIPPET_LINES - 1)
    return "\n".join(f"{i:5d} | {lines[i - 1]}" for i in range(start, end + 1))


def evidence_snippets(
    repo: Path, base_branch: str, evidence: list[str] | tuple[str, ...], max_items: int = 2
) -> str:
    """Code behind the top "changed by X, called by Y" evidence lines.

    For each item: the definition as it was on the merge-base vs. on the
    branch that changed it (unified diff), and the call site on the other
    branch. A call site that no longer matches the new signature comes first,
    then breaking signature changes (a new required parameter / field, or a
    removed one), then compatible ones, then body-only changes.
    """
    import difflib

    items: list[tuple[int, str]] = []
    for line in evidence:
        m = _EVIDENCE_CALL.match(line)
        if not m:
            continue
        name, changer, def_path = m["name"], m["changer"], m["def_path"]
        caller, call_path = m["caller"], m["call_path"]
        if not (def_path.endswith(".py") and call_path.endswith(".py")):
            continue
        try:
            mb = git_service.merge_base(repo, base_branch, changer) or base_branch
            old_src = git_service.show_file(repo, mb, def_path)
            new_src = git_service.show_file(repo, changer, def_path)
            call_src = git_service.show_file(repo, caller, call_path)
        except git_service.GitError:
            continue
        old_tree, new_tree, call_tree = _parse(old_src), _parse(new_src), _parse(call_src)
        new_node = _find_def(new_tree, name) if new_tree else None
        if new_node is None or call_tree is None or new_src is None or call_src is None:
            continue
        old_node = _find_def(old_tree, name) if old_tree else None
        change = signature_change(old_node, new_node)
        call = _first_call(call_tree, name)
        mismatch = call_mismatch(call, old_node, new_node) if call is not None else None
        if mismatch:
            change = f"{change}; {mismatch}"
        rank = (
            0 if mismatch else 1 if change and change.startswith("breaking") else 2 if change else 3
        )
        old_lines = _lines(old_src, old_node) if old_node is not None and old_src else []
        new_lines = _lines(new_src, new_node)
        diff = list(difflib.unified_diff(
            old_lines, new_lines, f"{def_path} (merge-base)", f"{def_path} ({changer})",
            n=3, lineterm="",
        ))[: MAX_SNIPPET_LINES + 2]
        site = _call_site(call_src, call_tree, name)
        if not diff or not site:
            continue
        head = f"[{change or 'body changed'}] {line}"
        text = "\n".join([head, *diff, f"call site on {caller} ({call_path}):", site])
        items.append((rank, text))
    items.sort(key=lambda item: item[0])
    return "\n\n".join(text for _, text in items[:max_items])
