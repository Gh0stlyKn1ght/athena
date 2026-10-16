"""Capability B & D: Deterministic AST Signature Differ, Caller Blast-Radius Scanner, and Doc-Drift Detector."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

from athena.git_inspector import RawRangeExtraction, _is_test_file, run_git_command
from athena.models import (
    CallerImpactItem,
    DocDriftItem,
    EvidenceRecord,
    LineSpan,
    PotentialBreakingChangeFinding,
    SymbolSignatureChange,
)
from athena.security import is_safe_repo_file


@dataclass
class _PyParamInfo:
    name: str
    kind: str  # POSITIONAL_ONLY, POSITIONAL_OR_KEYWORD, VAR_POSITIONAL, KEYWORD_ONLY, VAR_KEYWORD
    has_default: bool
    default_repr: str | None
    annotation_repr: str | None


@dataclass
class _PySymbolInfo:
    qualified_name: str
    short_name: str
    symbol_kind: str  # function, async_function, class, method
    start_line: int
    end_line: int
    signature_str: str
    params: list[_PyParamInfo] = field(default_factory=list)
    return_annotation: str | None = None
    decorators: list[str] = field(default_factory=list)
    bases: list[str] = field(default_factory=list)
    raised_exceptions: set[str] = field(default_factory=set)
    none_guarded_params: set[str] = field(default_factory=set)


@dataclass
class StructuralAnalysisResult:
    symbol_changes: list[SymbolSignatureChange] = field(default_factory=list)
    caller_impacts: dict[str, list[CallerImpactItem]] = field(default_factory=dict)
    doc_drift_items: list[DocDriftItem] = field(default_factory=list)
    untested_modified_symbols: list[str] = field(default_factory=list)
    deterministic_breaking_findings: list[PotentialBreakingChangeFinding] = field(default_factory=list)


def _unparse_safe(node: ast.AST | None) -> str | None:
    if node is None:
        return None
    try:
        return ast.unparse(node).strip()
    except Exception:
        return None


def _extract_raised_exceptions(func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Detect explicitly raised exception class names inside a function body (griffe#498)."""
    raised: set[str] = set()
    for child in ast.walk(func_node):
        if isinstance(child, ast.Raise) and child.exc is not None:
            if isinstance(child.exc, ast.Call):
                exc_name = _unparse_safe(child.exc.func)
                if exc_name:
                    raised.add(exc_name.split(".")[-1])
            elif isinstance(child.exc, ast.Name):
                raised.add(child.exc.id)
    return raised


def _extract_none_guarded_params(func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Detect `if param is None:` or `param = param or ...` body guards (griffe#469)."""
    guarded: set[str] = set()
    for child in ast.walk(func_node):
        if isinstance(child, ast.Compare):
            # Check `param is None` or `None is param`
            left_str = _unparse_safe(child.left)
            for op, comp in zip(child.ops, child.comparators):
                comp_str = _unparse_safe(comp)
                if isinstance(op, (ast.Is, ast.Eq)):
                    if comp_str == "None" and left_str:
                        guarded.add(left_str)
                    elif left_str == "None" and comp_str:
                        guarded.add(comp_str)
        elif isinstance(child, ast.Assign):
            # Check `if x is None: x = ...` or `x = default if x is None else x`
            if isinstance(child.value, ast.BoolOp) and isinstance(child.value.op, ast.Or):
                if child.value.values:
                    first = _unparse_safe(child.value.values[0])
                    if first:
                        guarded.add(first)
    return guarded


def _extract_py_params(args: ast.arguments, is_method: bool = False) -> list[_PyParamInfo]:
    params: list[_PyParamInfo] = []
    posonly = list(args.posonlyargs)
    regular = list(args.args)
    all_pos = posonly + regular
    num_pos_defaults = len(args.defaults)
    first_default_idx = len(all_pos) - num_pos_defaults

    for idx, arg in enumerate(all_pos):
        if is_method and idx == 0 and arg.arg in ("self", "cls"):
            continue
        has_def = idx >= first_default_idx
        def_node = args.defaults[idx - first_default_idx] if has_def else None
        kind = "POSITIONAL_ONLY" if idx < len(posonly) else "POSITIONAL_OR_KEYWORD"
        params.append(
            _PyParamInfo(
                name=arg.arg,
                kind=kind,
                has_default=has_def,
                default_repr=_unparse_safe(def_node) if has_def else None,
                annotation_repr=_unparse_safe(arg.annotation),
            )
        )

    if args.vararg is not None:
        params.append(
            _PyParamInfo(
                name=args.vararg.arg,
                kind="VAR_POSITIONAL",
                has_default=True,
                default_repr=None,
                annotation_repr=_unparse_safe(args.vararg.annotation),
            )
        )

    for kw_arg, kw_def in zip(args.kwonlyargs, args.kw_defaults):
        has_def = kw_def is not None
        params.append(
            _PyParamInfo(
                name=kw_arg.arg,
                kind="KEYWORD_ONLY",
                has_default=has_def,
                default_repr=_unparse_safe(kw_def) if has_def else None,
                annotation_repr=_unparse_safe(kw_arg.annotation),
            )
        )

    if args.kwarg is not None:
        params.append(
            _PyParamInfo(
                name=args.kwarg.arg,
                kind="VAR_KEYWORD",
                has_default=True,
                default_repr=None,
                annotation_repr=_unparse_safe(args.kwarg.annotation),
            )
        )

    return params


def _format_py_signature(
    name: str,
    params: list[_PyParamInfo],
    return_annotation: str | None,
    decorators: list[str],
    is_async: bool = False,
) -> str:
    parts: list[str] = []
    has_posonly = any(p.kind == "POSITIONAL_ONLY" for p in params)
    posonly_closed = False
    kw_only_started = False
    for idx, p in enumerate(params):
        if has_posonly and not posonly_closed and p.kind != "POSITIONAL_ONLY":
            parts.append("/")
            posonly_closed = True
        if p.kind == "KEYWORD_ONLY" and not kw_only_started:
            prev_was_varpos = idx > 0 and params[idx - 1].kind == "VAR_POSITIONAL"
            if not prev_was_varpos:
                parts.append("*")
            kw_only_started = True
        prefix = "*" if p.kind == "VAR_POSITIONAL" else ("**" if p.kind == "VAR_KEYWORD" else "")
        seg = f"{prefix}{p.name}"
        if p.annotation_repr:
            seg += f": {p.annotation_repr}"
        if p.has_default and p.default_repr is not None:
            seg += f" = {p.default_repr}"
        parts.append(seg)
    if has_posonly and not posonly_closed:
        parts.append("/")
    ret_str = f" -> {return_annotation}" if return_annotation else ""
    kw = "async def" if is_async else "def"
    dec_prefix = "".join(f"@{d} " for d in decorators) if decorators else ""
    return f"{dec_prefix}{kw} {name}({', '.join(parts)}){ret_str}"


def _parse_python_symbols(source: str) -> dict[str, _PySymbolInfo]:
    """Parse public Python functions, classes, and methods from source code."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}

    symbols: dict[str, _PySymbolInfo] = {}

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("_"):
                continue
            is_async = isinstance(node, ast.AsyncFunctionDef)
            decs = [d for d in (_unparse_safe(dec) for dec in node.decorator_list) if d]
            params = _extract_py_params(node.args, is_method=False)
            ret = _unparse_safe(node.returns)
            sig = _format_py_signature(node.name, params, ret, decs, is_async=is_async)
            symbols[node.name] = _PySymbolInfo(
                qualified_name=node.name,
                short_name=node.name,
                symbol_kind="async_function" if is_async else "function",
                start_line=getattr(node, "lineno", 1),
                end_line=getattr(node, "end_lineno", getattr(node, "lineno", 1)),
                signature_str=sig,
                params=params,
                return_annotation=ret,
                decorators=decs,
                raised_exceptions=_extract_raised_exceptions(node),
                none_guarded_params=_extract_none_guarded_params(node),
            )
        elif isinstance(node, ast.ClassDef):
            if node.name.startswith("_"):
                continue
            bases = [b for b in (_unparse_safe(base) for base in node.bases) if b]
            decs = [d for d in (_unparse_safe(dec) for dec in node.decorator_list) if d]
            base_sig = f"({', '.join(bases)})" if bases else ""
            dec_prefix = "".join(f"@{d} " for d in decs) if decs else ""
            symbols[node.name] = _PySymbolInfo(
                qualified_name=node.name,
                short_name=node.name,
                symbol_kind="class",
                start_line=getattr(node, "lineno", 1),
                end_line=getattr(node, "end_lineno", getattr(node, "lineno", 1)),
                signature_str=f"{dec_prefix}class {node.name}{base_sig}",
                decorators=decs,
                bases=bases,
            )
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if item.name.startswith("_") and item.name not in ("__init__", "__call__"):
                        continue
                    is_async_m = isinstance(item, ast.AsyncFunctionDef)
                    m_decs = [d for d in (_unparse_safe(dec) for dec in item.decorator_list) if d]
                    is_static = any("staticmethod" in d for d in m_decs)
                    m_params = _extract_py_params(item.args, is_method=not is_static)
                    m_ret = _unparse_safe(item.returns)
                    qname = f"{node.name}.{item.name}"
                    m_sig = _format_py_signature(qname, m_params, m_ret, m_decs, is_async=is_async_m)

                    is_setter_or_deleter = any(d.endswith((".setter", ".deleter")) for d in m_decs)
                    if qname in symbols and is_setter_or_deleter:
                        # Keep getter as primary symbol, record setter under qname.setter
                        qname_sub = f"{qname}.setter" if any(d.endswith(".setter") for d in m_decs) else f"{qname}.deleter"
                        symbols[qname_sub] = _PySymbolInfo(
                            qualified_name=qname_sub,
                            short_name=f"{item.name}.setter",
                            symbol_kind="method",
                            start_line=getattr(item, "lineno", 1),
                            end_line=getattr(item, "end_lineno", getattr(item, "lineno", 1)),
                            signature_str=m_sig,
                            params=m_params,
                            return_annotation=m_ret,
                            decorators=m_decs,
                            raised_exceptions=_extract_raised_exceptions(item),
                            none_guarded_params=_extract_none_guarded_params(item),
                        )
                        continue

                    symbols[qname] = _PySymbolInfo(
                        qualified_name=qname,
                        short_name=item.name,
                        symbol_kind="method",
                        start_line=getattr(item, "lineno", 1),
                        end_line=getattr(item, "end_lineno", getattr(item, "lineno", 1)),
                        signature_str=m_sig,
                        params=m_params,
                        return_annotation=m_ret,
                        decorators=m_decs,
                        raised_exceptions=_extract_raised_exceptions(item),
                        none_guarded_params=_extract_none_guarded_params(item),
                    )
    return symbols


def _compare_python_symbol(
    file_path: str,
    old_sym: _PySymbolInfo,
    new_sym: _PySymbolInfo,
) -> list[tuple[str, bool, str, str]]:
    """Return list of (change_kind, is_breaking_candidate, severity, details) for a symbol pair."""
    findings: list[tuple[str, bool, str, str]] = []

    if old_sym.symbol_kind == "class" and new_sym.symbol_kind == "class":
        if old_sym.bases != new_sym.bases:
            findings.append(
                (
                    "base_class_changed",
                    True,
                    "medium",
                    f"Class '{old_sym.qualified_name}' base classes changed from {old_sym.bases} to {new_sym.bases} (potential isinstance/inheritance break).",
                )
            )
        if set(old_sym.decorators) != set(new_sym.decorators):
            findings.append(
                (
                    "decorator_changed",
                    True,
                    "medium",
                    f"Class '{old_sym.qualified_name}' decorators changed from {old_sym.decorators} to {new_sym.decorators}.",
                )
            )
        return findings

    # Compare function/method parameters
    old_by_name = {p.name: p for p in old_sym.params if p.kind not in ("VAR_POSITIONAL", "VAR_KEYWORD")}
    new_by_name = {p.name: p for p in new_sym.params if p.kind not in ("VAR_POSITIONAL", "VAR_KEYWORD")}
    has_new_varargs = any(p.kind == "VAR_POSITIONAL" for p in new_sym.params)
    has_new_kwargs = any(p.kind == "VAR_KEYWORD" for p in new_sym.params)

    removed_params = [name for name in old_by_name if name not in new_by_name]
    if removed_params:
        if has_new_varargs and has_new_kwargs:
            findings.append(
                (
                    "signature_changed",
                    False,
                    "low",
                    f"Parameters {removed_params} removed from '{old_sym.qualified_name}', but absorbed by *args/**kwargs.",
                )
            )
        else:
            findings.append(
                (
                    "signature_changed",
                    True,
                    "high",
                    f"Removed parameter(s) {removed_params} from '{old_sym.qualified_name}' without *args/**kwargs absorption.",
                )
            )

    added_required = [
        p.name for p in new_sym.params if p.name not in old_by_name and not p.has_default and p.kind not in ("VAR_POSITIONAL", "VAR_KEYWORD")
    ]
    if added_required:
        findings.append(
            (
                "signature_changed",
                True,
                "high",
                f"Added required parameter(s) {added_required} without default values to '{old_sym.qualified_name}'.",
            )
        )

    added_optional = [
        p.name for p in new_sym.params if p.name not in old_by_name and p.has_default and p.kind not in ("VAR_POSITIONAL", "VAR_KEYWORD")
    ]
    if added_optional and not findings:
        findings.append(
            (
                "signature_changed",
                False,
                "low",
                f"Added backward-compatible optional parameter(s) {added_optional} with default values to '{old_sym.qualified_name}'.",
            )
        )

    # Check common parameters for lost defaults, default=None guard (griffe#469), or kind changes
    old_pos_order = [p.name for p in old_sym.params if p.kind in ("POSITIONAL_ONLY", "POSITIONAL_OR_KEYWORD")]
    new_pos_order = [p.name for p in new_sym.params if p.kind in ("POSITIONAL_ONLY", "POSITIONAL_OR_KEYWORD")]
    common_pos_old = [n for n in old_pos_order if n in new_pos_order]
    common_pos_new = [n for n in new_pos_order if n in old_pos_order]
    if common_pos_old != common_pos_new:
        findings.append(
            (
                "signature_changed",
                True,
                "high",
                f"Positional parameter order changed in '{old_sym.qualified_name}' from {common_pos_old} to {common_pos_new}.",
            )
        )

    for name, old_p in old_by_name.items():
        if name not in new_by_name:
            continue
        new_p = new_by_name[name]
        if old_p.has_default and not new_p.has_default:
            findings.append(
                (
                    "signature_changed",
                    True,
                    "high",
                    f"Parameter '{name}' in '{old_sym.qualified_name}' lost its default value ({old_p.default_repr}) and is now required.",
                )
            )
        elif (
            old_p.has_default
            and new_p.has_default
            and old_p.default_repr != new_p.default_repr
        ):
            if new_p.default_repr == "None" and name in new_sym.none_guarded_params:
                findings.append(
                    (
                        "signature_changed",
                        False,
                        "low",
                        f"Parameter '{name}' default changed from {old_p.default_repr} to None in '{old_sym.qualified_name}', but function body contains a None-fallback guard (griffe#469 pattern).",
                    )
                )
            else:
                findings.append(
                    (
                        "signature_changed",
                        True,
                        "medium",
                        f"Default value for parameter '{name}' in '{old_sym.qualified_name}' changed from {old_p.default_repr} to {new_p.default_repr}.",
                    )
                )
        if old_p.kind != new_p.kind:
            findings.append(
                (
                    "signature_changed",
                    True,
                    "medium",
                    f"Parameter '{name}' kind in '{old_sym.qualified_name}' changed from {old_p.kind} to {new_p.kind}.",
                )
            )

    # Check return type annotation change (diff.py:L699 gap)
    if old_sym.return_annotation != new_sym.return_annotation and old_sym.return_annotation is not None:
        findings.append(
            (
                "return_type_changed",
                True,
                "medium",
                f"Return type annotation of '{old_sym.qualified_name}' changed from '{old_sym.return_annotation}' to '{new_sym.return_annotation}'.",
            )
        )

    # Check decorator removal/change (diff.py:L503 gap)
    if old_sym.decorators != new_sym.decorators:
        findings.append(
            (
                "decorator_changed",
                True,
                "medium",
                f"Decorators on '{old_sym.qualified_name}' changed from {old_sym.decorators} to {new_sym.decorators}.",
            )
        )

    # Check newly raised exceptions in function body (griffe#498 gap)
    newly_raised = sorted(new_sym.raised_exceptions - old_sym.raised_exceptions)
    if newly_raised:
        findings.append(
            (
                "exception_raised_added",
                True,
                "medium",
                f"Function '{old_sym.qualified_name}' now raises new exception type(s) {newly_raised} not raised in base revision.",
            )
        )

    return findings


def _extract_py_imports(tree: ast.AST) -> dict[str, str]:
    """Extract imported symbols and modules mapping alias/name to full import source path."""
    imports: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                asname = alias.asname or alias.name
                imports[asname] = alias.name
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            for alias in node.names:
                asname = alias.asname or alias.name
                imports[asname] = f"{mod}.{alias.name}" if mod else alias.name
    return imports


_TS_EXPORT_FUNC_RE = re.compile(
    r"^\s*export\s+(?:default\s+)?(?:async\s+)?function\s+(?P<name>[A-Za-z0-9_$]+)(?:<[^>]*>)?\s*\((?P<params>[^)]*)\)(?:\s*:\s*(?P<ret>[^{;]+))?",
    re.MULTILINE,
)
_TS_EXPORT_ARROW_RE = re.compile(
    r"^\s*export\s+(?:const|let|var)\s+(?P<name>[A-Za-z0-9_$]+)\s*=\s*(?:async\s*)?(?:<[^>]*>)?\s*\((?P<params>[^)]*)\)(?:\s*:\s*(?P<ret>[^{;=>]+))?\s*=>",
    re.MULTILINE,
)
_TS_EXPORT_CLASS_RE = re.compile(
    r"^\s*export\s+(?:default\s+)?class\s+(?P<name>[A-Za-z0-9_$]+)(?:<[^>]*>)?(?:\s+extends\s+(?P<base>[A-Za-z0-9_$.<>]+))?",
    re.MULTILINE,
)
_TS_EXPORT_TYPE_RE = re.compile(
    r"^\s*export\s+(?P<kind>interface|type|enum)\s+(?P<name>[A-Za-z0-9_$]+)(?:[<={\s])",
    re.MULTILINE,
)


def _parse_ts_js_symbols(source: str) -> dict[str, tuple[str, int, str, list[str], str | None]]:
    """Extract exported functions, classes, interfaces, enums, and arrow functions from TypeScript/JavaScript files.
    Uses pure-Python structural pattern analysis without requiring external Node.js or native compiler dependencies.
    Returns dict: name -> (kind, line_no, signature_str, param_list, return_type)
    """
    symbols: dict[str, tuple[str, int, str, list[str], str | None]] = {}
    for match in _TS_EXPORT_FUNC_RE.finditer(source):
        name = match.group("name")
        params_raw = match.group("params").strip()
        ret_raw = (match.group("ret") or "").strip() or None
        line_no = source.count("\n", 0, match.start()) + 1
        param_items = [p.strip() for p in params_raw.split(",") if p.strip()]
        sig = f"export function {name}({params_raw})" + (f": {ret_raw}" if ret_raw else "")
        symbols[name] = ("function", line_no, sig, param_items, ret_raw)

    for match in _TS_EXPORT_ARROW_RE.finditer(source):
        name = match.group("name")
        params_raw = match.group("params").strip()
        ret_raw = (match.group("ret") or "").strip() or None
        line_no = source.count("\n", 0, match.start()) + 1
        param_items = [p.strip() for p in params_raw.split(",") if p.strip()]
        sig = f"export const {name} = ({params_raw})" + (f": {ret_raw}" if ret_raw else "") + " =>"
        symbols[name] = ("function", line_no, sig, param_items, ret_raw)

    for match in _TS_EXPORT_CLASS_RE.finditer(source):
        name = match.group("name")
        base = (match.group("base") or "").strip() or None
        line_no = source.count("\n", 0, match.start()) + 1
        sig = f"export class {name}" + (f" extends {base}" if base else "")
        symbols[name] = ("class", line_no, sig, [], base)

    for match in _TS_EXPORT_TYPE_RE.finditer(source):
        name = match.group("name")
        kind_name = match.group("kind").lower()
        line_no = source.count("\n", 0, match.start()) + 1
        sig = f"export {kind_name} {name}"
        symbols[name] = (kind_name, line_no, sig, [], None)

    return symbols


def _list_repo_files_at_target(repo_path: Path, target_sha: str) -> list[str]:
    if target_sha == "WORKING_TREE":
        proc = run_git_command(repo_path, ["ls-files"], check=False)
    else:
        proc = run_git_command(repo_path, ["ls-tree", "-r", "--name-only", target_sha], check=False)
    if proc.returncode != 0:
        return []
    return [f.strip().replace("\\", "/") for f in proc.stdout.splitlines() if f.strip()]


def _scan_callers_and_tests_and_docs(
    extraction: RawRangeExtraction,
    symbol_changes: list[SymbolSignatureChange],
    modified_symbol_names: set[str],
) -> tuple[dict[str, list[CallerImpactItem]], list[str], list[DocDriftItem]]:
    """Scan repository files for call sites of breaking symbols, test coverage, and Markdown doc drift."""
    repo_path = extraction.repo_path
    target_sha = extraction.resolved_target_sha
    all_files = _list_repo_files_at_target(repo_path, target_sha)

    breaking_short_names: dict[str, str] = {}
    breaking_by_qname: dict[str, SymbolSignatureChange] = {}
    for sc in symbol_changes:
        if sc.is_breaking_candidate:
            short = sc.symbol_name.split(".")[-1]
            if short not in ("__init__", "__call__"):
                breaking_short_names[short] = sc.symbol_name
            else:
                cls_name = sc.symbol_name.split(".")[0]
                breaking_short_names[cls_name] = sc.symbol_name
            breaking_by_qname[sc.symbol_name] = sc

    caller_impacts: dict[str, list[CallerImpactItem]] = {sc.symbol_name: [] for sc in symbol_changes if sc.is_breaking_candidate}
    tested_symbols: set[str] = set()
    doc_drift_items: list[DocDriftItem] = []

    changed_doc_paths = {f.file_path for f in extraction.changed_files if f.is_doc_file}

    for rel_path in all_files[:250]:  # Bound file scan for 6GB VRAM / laptop speed
        is_py = rel_path.endswith(".py")
        is_ts = rel_path.endswith((".ts", ".tsx", ".js", ".jsx"))
        is_md = rel_path.endswith((".md", ".rst"))
        if not (is_py or is_ts or is_md):
            continue

        # Read file content from target_blobs cache or working tree / git
        content = extraction.target_blobs.get(rel_path)
        if content is None:
            candidate = repo_path / rel_path
            if candidate.exists() and candidate.is_file() and is_safe_repo_file(repo_path, candidate):
                try:
                    if candidate.stat().st_size <= 256 * 1024:
                        content = candidate.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    content = None
        if not content:
            continue

        is_test = _is_test_file(rel_path)
        if is_test:
            for sym_qname in modified_symbol_names:
                short_n = sym_qname.split(".")[-1]
                if short_n in ("__init__", "__call__"):
                    short_n = sym_qname.split(".")[0]
                if re.search(rf"\b{re.escape(short_n)}\b", content):
                    tested_symbols.add(sym_qname)

        if is_py and breaking_short_names:
            try:
                tree = ast.parse(content)
                lines = content.splitlines()
                file_imports = _extract_py_imports(tree)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Call):
                        callee_name: str | None = None
                        if isinstance(node.func, ast.Name):
                            callee_name = node.func.id
                        elif isinstance(node.func, ast.Attribute):
                            callee_name = node.func.attr
                        target_short_name: str | None = None
                        if callee_name and callee_name in breaking_short_names:
                            target_short_name = callee_name
                        elif callee_name and callee_name in file_imports:
                            imported_target = file_imports[callee_name].split(".")[-1]
                            if imported_target in breaking_short_names:
                                target_short_name = imported_target

                        if target_short_name:
                            qname = breaking_short_names[target_short_name]
                            sc_obj = breaking_by_qname[qname]
                            # Skip self-definition line in the defining file
                            lineno = getattr(node, "lineno", 1)
                            if rel_path == sc_obj.file_path and sc_obj.target_line_span and sc_obj.target_line_span.start_line <= lineno <= sc_obj.target_line_span.end_line:
                                continue
                            snippet = lines[lineno - 1].strip() if 1 <= lineno <= len(lines) else callee_name
                            # Ground resolution confidence in whether this file actually imported the symbol/module
                            def_mod_stem = Path(sc_obj.file_path).stem
                            has_explicit_import = (callee_name in file_imports) or any(def_mod_stem in v for v in file_imports.values())
                            conf_level = "exact_ast" if (has_explicit_import or rel_path == sc_obj.file_path) else "heuristic_name_match"
                            if len(caller_impacts.get(qname, [])) < 10:
                                caller_impacts.setdefault(qname, []).append(
                                    CallerImpactItem(
                                        caller_file=rel_path,
                                        caller_symbol=callee_name,
                                        line_number=lineno,
                                        call_snippet=snippet[:160],
                                        resolution_confidence=conf_level,
                                    )
                                )
            except SyntaxError:
                pass

        elif is_ts and breaking_short_names:
            lines = content.splitlines()
            for short_n, qname in breaking_short_names.items():
                sc_obj = breaking_by_qname[qname]
                for idx, line in enumerate(lines, start=1):
                    if rel_path == sc_obj.file_path and sc_obj.target_line_span and sc_obj.target_line_span.start_line == idx:
                        continue
                    if re.search(rf"\b{re.escape(short_n)}\s*\(", line):
                        if len(caller_impacts.get(qname, [])) < 10:
                            caller_impacts.setdefault(qname, []).append(
                                CallerImpactItem(
                                    caller_file=rel_path,
                                    caller_symbol=short_n,
                                    line_number=idx,
                                    call_snippet=line.strip()[:160],
                                    resolution_confidence="heuristic_name_match",
                                )
                            )

        elif is_md and breaking_short_names:
            # Check Markdown files that were NOT updated (or still reference stale removed/changed signatures)
            lines = content.splitlines()
            for short_n, qname in breaking_short_names.items():
                sc_obj = breaking_by_qname[qname]
                for idx, line in enumerate(lines, start=1):
                    if re.search(rf"\b{re.escape(short_n)}\s*\(", line) or f"`{short_n}`" in line:
                        # If the doc file wasn't modified or still contains the old call pattern
                        if rel_path not in changed_doc_paths or sc_obj.change_kind == "removed":
                            ev_id = sc_obj.evidence_ids[0] if sc_obj.evidence_ids else None
                            doc_drift_items.append(
                                DocDriftItem(
                                    doc_file=rel_path,
                                    line_number=idx,
                                    referenced_symbol=qname,
                                    snippet=line.strip()[:160],
                                    drift_reason=(
                                        f"Documentation in '{rel_path}:{idx}' references '{short_n}', "
                                        f"whose signature/availability changed ({sc_obj.change_kind}: {sc_obj.details})."
                                    ),
                                    evidence_ids=[ev_id] if ev_id else [],
                                )
                            )
                            break

    untested = sorted(modified_symbol_names - tested_symbols)
    return caller_impacts, untested, doc_drift_items


def run_structural_code_analysis(extraction: RawRangeExtraction) -> StructuralAnalysisResult:
    """Run deterministic Python AST and TS/JS signature diffing, caller blast-radius scan, and doc-drift detection."""
    symbol_changes: list[SymbolSignatureChange] = []
    modified_symbol_names: set[str] = set()

    next_ev_num = len(extraction.evidence_registry) + 1

    def _register_symbol_evidence(
        file_delta_path: str,
        base_span: LineSpan | None,
        target_span: LineSpan | None,
        excerpt: str,
        reason: str,
    ) -> str:
        nonlocal next_ev_num
        eid = f"ev_{next_ev_num:03d}"
        next_ev_num += 1
        # Find commit hash & message for this file
        c_hash = extraction.resolved_target_sha
        c_msg = "Working tree or revision diff"
        for c in extraction.commits:
            if file_delta_path in [f.replace("\\", "/") for f in c.changed_files]:
                c_hash = c.commit_hash
                c_msg = c.subject
                break
        if c_hash == "WORKING_TREE" and extraction.commits:
            c_hash = extraction.commits[0].commit_hash
            c_msg = extraction.commits[0].subject
        extraction.evidence_registry[eid] = EvidenceRecord(
            evidence_id=eid,
            commit_hash=c_hash,
            commit_message=c_msg,
            file_path=file_delta_path,
            base_line_span=base_span,
            target_line_span=target_span,
            diff_hunk_header=f"AST Symbol Diff ({file_delta_path})",
            diff_excerpt=excerpt[:600],
            reason_supports_finding=reason,
            confidence="high",
        )
        return eid

    for fdelta in extraction.changed_files:
        fpath = fdelta.file_path
        if fdelta.is_test_file or fdelta.is_doc_file:
            continue

        base_src = extraction.base_blobs.get(fpath, "")
        target_src = extraction.target_blobs.get(fpath, "")

        if fpath.endswith(".py"):
            old_syms = _parse_python_symbols(base_src) if base_src else {}
            new_syms = _parse_python_symbols(target_src) if target_src else {}

            # 1. Removed symbols
            for qname, old_s in old_syms.items():
                if qname not in new_syms:
                    b_span = LineSpan(start_line=old_s.start_line, end_line=old_s.end_line)
                    reason = f"Public {old_s.symbol_kind} '{qname}' was removed from {fpath}."
                    eid = _register_symbol_evidence(
                        fpath,
                        b_span,
                        None,
                        f"- {old_s.signature_str}",
                        reason,
                    )
                    sc = SymbolSignatureChange(
                        file_path=fpath,
                        symbol_name=qname,
                        symbol_kind=old_s.symbol_kind,  # type: ignore[arg-type]
                        change_kind="removed",
                        old_signature=old_s.signature_str,
                        new_signature=None,
                        is_breaking_candidate=True,
                        severity="high",
                        details=reason,
                        base_line_span=b_span,
                        target_line_span=None,
                        evidence_ids=[eid, *fdelta.evidence_ids[:1]],
                    )
                    symbol_changes.append(sc)
                    fdelta.symbol_changes.append(sc)
                    modified_symbol_names.add(qname)

            # 2. Added symbols
            for qname, new_s in new_syms.items():
                if qname not in old_syms:
                    t_span = LineSpan(start_line=new_s.start_line, end_line=new_s.end_line)
                    reason = f"Public {new_s.symbol_kind} '{qname}' was added in {fpath}."
                    eid = _register_symbol_evidence(
                        fpath,
                        None,
                        t_span,
                        f"+ {new_s.signature_str}",
                        reason,
                    )
                    sc = SymbolSignatureChange(
                        file_path=fpath,
                        symbol_name=qname,
                        symbol_kind=new_s.symbol_kind,  # type: ignore[arg-type]
                        change_kind="added",
                        old_signature=None,
                        new_signature=new_s.signature_str,
                        is_breaking_candidate=False,
                        severity="low",
                        details=reason,
                        base_line_span=None,
                        target_line_span=t_span,
                        evidence_ids=[eid],
                    )
                    symbol_changes.append(sc)
                    fdelta.symbol_changes.append(sc)
                    modified_symbol_names.add(qname)

            # 3. Modified symbols
            for qname in old_syms.keys() & new_syms.keys():
                old_s = old_syms[qname]
                new_s = new_syms[qname]
                diffs = _compare_python_symbol(fpath, old_s, new_s)
                for change_kind, is_brk, sev, details in diffs:
                    b_span = LineSpan(start_line=old_s.start_line, end_line=old_s.end_line)
                    t_span = LineSpan(start_line=new_s.start_line, end_line=new_s.end_line)
                    eid = _register_symbol_evidence(
                        fpath,
                        b_span,
                        t_span,
                        f"- {old_s.signature_str}\n+ {new_s.signature_str}",
                        details,
                    )
                    sc = SymbolSignatureChange(
                        file_path=fpath,
                        symbol_name=qname,
                        symbol_kind=new_s.symbol_kind,  # type: ignore[arg-type]
                        change_kind=change_kind,  # type: ignore[arg-type]
                        old_signature=old_s.signature_str,
                        new_signature=new_s.signature_str,
                        is_breaking_candidate=is_brk,
                        severity=sev,  # type: ignore[arg-type]
                        details=details,
                        base_line_span=b_span,
                        target_line_span=t_span,
                        evidence_ids=[eid, *fdelta.evidence_ids[:1]],
                    )
                    symbol_changes.append(sc)
                    fdelta.symbol_changes.append(sc)
                    modified_symbol_names.add(qname)

        elif fpath.endswith((".ts", ".tsx", ".js", ".jsx")):
            old_ts = _parse_ts_js_symbols(base_src) if base_src else {}
            new_ts = _parse_ts_js_symbols(target_src) if target_src else {}
            for name, (kind, old_line, old_sig, old_params, old_ret) in old_ts.items():
                if name not in new_ts:
                    b_span = LineSpan(start_line=old_line, end_line=old_line)
                    reason = f"Exported {kind} '{name}' was removed from {fpath}."
                    eid = _register_symbol_evidence(fpath, b_span, None, f"- {old_sig}", reason)
                    sc = SymbolSignatureChange(
                        file_path=fpath,
                        symbol_name=name,
                        symbol_kind=kind,  # type: ignore[arg-type]
                        change_kind="removed",
                        old_signature=old_sig,
                        new_signature=None,
                        is_breaking_candidate=True,
                        severity="high",
                        details=reason,
                        base_line_span=b_span,
                        evidence_ids=[eid],
                    )
                    symbol_changes.append(sc)
                    fdelta.symbol_changes.append(sc)
                    modified_symbol_names.add(name)
                else:
                    _, new_line, new_sig, new_params, new_ret = new_ts[name]
                    if old_sig != new_sig:
                        b_span = LineSpan(start_line=old_line, end_line=old_line)
                        t_span = LineSpan(start_line=new_line, end_line=new_line)
                        # Breaking if parameter count increased without '=' or '?' or decreased
                        old_req = [p for p in old_params if "=" not in p and "?" not in p]
                        new_req = [p for p in new_params if "=" not in p and "?" not in p]
                        is_brk = len(new_req) > len(old_req) or len(new_params) < len(old_params) or (old_ret != new_ret and old_ret is not None)
                        reason = f"Exported {kind} '{name}' signature changed from '{old_sig}' to '{new_sig}'."
                        eid = _register_symbol_evidence(fpath, b_span, t_span, f"- {old_sig}\n+ {new_sig}", reason)
                        sc = SymbolSignatureChange(
                            file_path=fpath,
                            symbol_name=name,
                            symbol_kind=kind,  # type: ignore[arg-type]
                            change_kind="signature_changed",
                            old_signature=old_sig,
                            new_signature=new_sig,
                            is_breaking_candidate=is_brk,
                            severity="high" if is_brk else "low",
                            details=reason,
                            base_line_span=b_span,
                            target_line_span=t_span,
                            evidence_ids=[eid],
                        )
                        symbol_changes.append(sc)
                        fdelta.symbol_changes.append(sc)
                        modified_symbol_names.add(name)

    caller_impacts, untested_symbols, doc_drift_items = _scan_callers_and_tests_and_docs(
        extraction, symbol_changes, modified_symbol_names
    )

    # Assemble deterministic PotentialBreakingChangeFinding objects (requires human review!)
    breaking_findings: list[PotentialBreakingChangeFinding] = []
    b_idx = 1
    for sc in symbol_changes:
        if not sc.is_breaking_candidate:
            continue
        fid = f"brk_{b_idx:03d}"
        b_idx += 1
        callers = caller_impacts.get(sc.symbol_name, [])
        related_drift = [d for d in doc_drift_items if d.referenced_symbol == sc.symbol_name]

        observed_facts = [
            f"In '{sc.file_path}', symbol '{sc.symbol_name}' ({sc.symbol_kind}) underwent '{sc.change_kind}'.",
            sc.details,
        ]
        if sc.old_signature:
            observed_facts.append(f"Base signature: `{sc.old_signature}`")
        if sc.new_signature:
            observed_facts.append(f"Target signature: `{sc.new_signature}`")
        if callers:
            observed_facts.append(
                f"Detected {len(callers)} downstream call site(s) in repository (e.g., {', '.join(f'{c.caller_file}:{c.line_number}' for c in callers[:3])})."
            )
        if related_drift:
            observed_facts.append(
                f"Detected {len(related_drift)} potentially stale documentation reference(s) (e.g., {', '.join(f'{d.doc_file}:{d.line_number}' for d in related_drift[:2])})."
            )

        risks = [
            "Requires human review: External callers or plugins invoking the previous signature may fail at runtime with TypeError, AttributeError, or unexpected behavior.",
        ]
        if sc.symbol_name in untested_symbols:
            risks.append(f"Symbol '{sc.symbol_name}' has no detected references in the repository test suite.")
        if related_drift:
            risks.append("Documentation examples referencing this symbol may fail when copy-pasted by users.")

        breaking_findings.append(
            PotentialBreakingChangeFinding(
                finding_id=fid,
                title=f"Potential Breaking Change in `{sc.symbol_name}` ({sc.change_kind}) — Requires Human Review",
                severity=sc.severity,
                confidence="high" if sc.change_kind in ("removed", "signature_changed") else "medium",
                uncertainty_notes="Static AST signature analysis verifies structural contract changes, but runtime dynamic dispatch or external consumer usage requires human review.",
                affected_symbol=sc.symbol_name,
                file_path=sc.file_path,
                change_kind=sc.change_kind,
                old_signature=sc.old_signature,
                new_signature=sc.new_signature,
                observed_facts=observed_facts,
                model_interpretation=(
                    f"Deterministic AST analysis flagged `{sc.symbol_name}` in `{sc.file_path}` as a potential breaking change ({sc.details}). "
                    "Review callers and migration notes before release."
                ),
                potential_risks_requiring_verification=risks,
                affected_callers=callers,
                doc_drift=related_drift,
                evidence_ids=list(dict.fromkeys(sc.evidence_ids)),
                evidence=[extraction.evidence_registry[eid] for eid in dict.fromkeys(sc.evidence_ids) if eid in extraction.evidence_registry],
            )
        )

    # Also check commits that explicitly declare BREAKING CHANGE in commit message even if no AST symbol matched
    for c in extraction.commits:
        if c.declares_breaking_in_message:
            already_covered = any(
                any(ev.commit_hash == c.commit_hash for ev in bf.evidence) for bf in breaking_findings
            )
            if not already_covered:
                eid = f"ev_{next_ev_num:03d}"
                next_ev_num += 1
                target_file = c.changed_files[0] if c.changed_files else "COMMIT_MESSAGE"
                extraction.evidence_registry[eid] = EvidenceRecord(
                    evidence_id=eid,
                    commit_hash=c.commit_hash,
                    commit_message=c.subject,
                    file_path=target_file,
                    diff_excerpt=f"{c.subject}\n{c.body}".strip()[:500],
                    reason_supports_finding="Commit message explicitly declares a breaking change ('!' or 'BREAKING CHANGE:').",
                    confidence="medium",
                )
                fid = f"brk_{b_idx:03d}"
                b_idx += 1
                breaking_findings.append(
                    PotentialBreakingChangeFinding(
                        finding_id=fid,
                        title=f"Commit `{c.short_hash}` Declares Potential Breaking Change — Requires Human Review",
                        severity="medium",
                        confidence="medium",
                        uncertainty_notes="Declared via Conventional Commit marker ('!' or 'BREAKING CHANGE:'); verify actual behavioral impact in diff.",
                        affected_symbol=None,
                        file_path=target_file,
                        change_kind="declared_in_commit",
                        observed_facts=[
                            f"Commit `{c.commit_hash[:12]}` ('{c.subject}') includes an explicit breaking-change indicator.",
                            f"Modified files in commit: {', '.join(c.changed_files[:5]) or 'none'}.",
                        ],
                        model_interpretation="Commit author explicitly marked this change as potentially breaking.",
                        potential_risks_requiring_verification=[
                            "Requires human review to confirm behavioral migration requirements for downstream users."
                        ],
                        evidence_ids=[eid],
                        evidence=[extraction.evidence_registry[eid]],
                    )
                )

    return StructuralAnalysisResult(
        symbol_changes=symbol_changes,
        caller_impacts=caller_impacts,
        doc_drift_items=doc_drift_items,
        untested_modified_symbols=untested_symbols,
        deterministic_breaking_findings=breaking_findings,
    )


def analyze_python_module_diff(
    old_code: str,
    new_code: str,
    file_path: str = "module.py",
) -> StructuralAnalysisResult:
    """Analyze changes between two versions of a Python source file directly."""
    old_syms = _parse_python_symbols(old_code) if old_code else {}
    new_syms = _parse_python_symbols(new_code) if new_code else {}

    symbol_changes: list[SymbolSignatureChange] = []

    # 1. Removed symbols
    for qname, old_s in old_syms.items():
        if qname not in new_syms:
            b_span = LineSpan(start_line=old_s.start_line, end_line=old_s.end_line)
            reason = f"Public {old_s.symbol_kind} '{qname}' was removed from {file_path}."
            sc = SymbolSignatureChange(
                file_path=file_path,
                symbol_name=qname,
                symbol_kind=old_s.symbol_kind,  # type: ignore[arg-type]
                change_kind="removed",
                old_signature=old_s.signature_str,
                new_signature=None,
                is_breaking_candidate=True,
                severity="high",
                details=reason,
                base_line_span=b_span,
                target_line_span=None,
                evidence_ids=[],
            )
            symbol_changes.append(sc)

    # 2. Added symbols
    for qname, new_s in new_syms.items():
        if qname not in old_syms:
            t_span = LineSpan(start_line=new_s.start_line, end_line=new_s.end_line)
            reason = f"Public {new_s.symbol_kind} '{qname}' was added in {file_path}."
            sc = SymbolSignatureChange(
                file_path=file_path,
                symbol_name=qname,
                symbol_kind=new_s.symbol_kind,  # type: ignore[arg-type]
                change_kind="added",
                old_signature=None,
                new_signature=new_s.signature_str,
                is_breaking_candidate=False,
                severity="low",
                details=reason,
                base_line_span=None,
                target_line_span=t_span,
                evidence_ids=[],
            )
            symbol_changes.append(sc)

    # 3. Modified symbols
    for qname in old_syms.keys() & new_syms.keys():
        old_s = old_syms[qname]
        new_s = new_syms[qname]
        diffs = _compare_python_symbol(file_path, old_s, new_s)
        for change_kind, is_brk, sev, details in diffs:
            b_span = LineSpan(start_line=old_s.start_line, end_line=old_s.end_line)
            t_span = LineSpan(start_line=new_s.start_line, end_line=new_s.end_line)
            sc = SymbolSignatureChange(
                file_path=file_path,
                symbol_name=qname,
                symbol_kind=new_s.symbol_kind,  # type: ignore[arg-type]
                change_kind=change_kind,  # type: ignore[arg-type]
                old_signature=old_s.signature_str,
                new_signature=new_s.signature_str,
                is_breaking_candidate=is_brk,
                severity=sev,  # type: ignore[arg-type]
                details=details,
                base_line_span=b_span,
                target_line_span=t_span,
                evidence_ids=[],
            )
            symbol_changes.append(sc)

    return StructuralAnalysisResult(symbol_changes=symbol_changes)


def find_affected_callers_in_tree(
    repo_path: Path,
    symbol_name: str,
) -> list[CallerImpactItem]:
    """Scan tree for AST call sites of symbol_name across Python files."""
    callers: list[CallerImpactItem] = []
    short_name = symbol_name.split(".")[-1]

    for py_file in repo_path.rglob("*.py"):
        try:
            content = py_file.read_text(encoding="utf-8", errors="replace")
            tree = ast.parse(content)
            lines = content.splitlines()
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    callee_name = None
                    if isinstance(node.func, ast.Name):
                        callee_name = node.func.id
                    elif isinstance(node.func, ast.Attribute):
                        callee_name = node.func.attr
                    if callee_name == short_name:
                        line_no = getattr(node, "lineno", 1)
                        snippet = lines[line_no - 1].strip() if line_no <= len(lines) else ""
                        rel_path = str(py_file.relative_to(repo_path)).replace("\\", "/")
                        callers.append(
                            CallerImpactItem(
                                caller_file=rel_path,
                                caller_symbol=short_name,
                                line_number=line_no,
                                call_snippet=snippet,
                                resolution_confidence="exact_ast",
                            )
                        )
        except Exception:
            continue
    return callers


def detect_python_doc_drift(
    code: str,
    file_path: str = "module.py",
) -> list[DocDriftItem]:
    """Detect discrepancies between docstring parameters and actual function signatures."""
    drift_items: list[DocDriftItem] = []
    try:
        tree = ast.parse(code)
    except Exception:
        return drift_items

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node)
            if not doc:
                continue
            actual_params = {arg.arg for arg in node.args.args + node.args.posonlyargs + node.args.kwonlyargs}
            # Search for :param <name>: or param <name>:
            param_matches = re.findall(r":param\s+([A-Za-z0-9_]+):", doc)
            for p in param_matches:
                if p not in actual_params:
                    drift_items.append(
                        DocDriftItem(
                            doc_file=file_path,
                            line_number=getattr(node, "lineno", 1),
                            referenced_symbol=node.name,
                            snippet=f":param {p}:",
                            drift_reason=f"Docstring for '{node.name}' documents parameter '{p}' which does not exist in function signature.",
                            evidence_ids=[],
                        )
                    )
    return drift_items

