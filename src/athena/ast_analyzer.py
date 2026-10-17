"""Capability B & D: Deterministic AST Signature Differ, Caller Blast-Radius Scanner, Dynamic Call Detector, and Doc-Drift Detector."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

from athena.git_inspector import (
    RawRangeExtraction,
    _is_test_file,
    read_blob_at_revision,
    run_git_command,
)
from athena.models import (
    AnalysisCoverageReport,
    CallerImpactItem,
     ConfidenceLevel,
    DocDriftItem,
    EvidenceRecord,
    LineSpan,
    PotentialBreakingChangeFinding,
    SymbolSignatureChange,
    UnresolvedDynamicCallItem,
)
from athena.security import is_safe_repo_file

_ENUM_BASE_NAMES = {"Enum", "IntEnum", "StrEnum", "Flag", "IntFlag", "ReprEnum"}


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
    symbol_kind: str  # function, async_function, class, method, enum, constant
    start_line: int
    end_line: int
    signature_str: str
    params: list[_PyParamInfo] = field(default_factory=list)
    return_annotation: str | None = None
    decorators: list[str] = field(default_factory=list)
    bases: list[str] = field(default_factory=list)
    raised_exceptions: set[str] = field(default_factory=set)
    none_guarded_params: set[str] = field(default_factory=set)
    enum_members: dict[str, str] = field(default_factory=dict)
    body_ast_dump: str = ""
    is_public_export: bool = True
    export_reason: str = ""


@dataclass
class StructuralAnalysisResult:
    symbol_changes: list[SymbolSignatureChange] = field(default_factory=list)
    caller_impacts: dict[str, list[CallerImpactItem]] = field(default_factory=dict)
    doc_drift_items: list[DocDriftItem] = field(default_factory=list)
    untested_modified_symbols: list[str] = field(default_factory=list)
    unresolved_dynamic_calls: list[UnresolvedDynamicCallItem] = field(default_factory=list)
    analysis_coverage: AnalysisCoverageReport = field(default_factory=AnalysisCoverageReport)
    deterministic_breaking_findings: list[PotentialBreakingChangeFinding] = field(default_factory=list)


def _unparse_safe(node: ast.AST | None) -> str | None:
    if node is None:
        return None
    try:
        return ast.unparse(node).strip()
    except Exception:
        return None


def _compute_body_ast_dump(func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Compute a normalized AST dump of function body statements (ignoring leading docstring)."""
    stmts = list(func_node.body)
    if (
        stmts
        and isinstance(stmts[0], ast.Expr)
        and isinstance(stmts[0].value, ast.Constant)
        and isinstance(stmts[0].value.value, str)
    ):
        stmts = stmts[1:]
    try:
        return "\n".join(ast.dump(s, include_attributes=False) for s in stmts)
    except Exception:
        return ""


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
            left_str = _unparse_safe(child.left)
            for op, comp in zip(child.ops, child.comparators):
                comp_str = _unparse_safe(comp)
                if isinstance(op, (ast.Is, ast.Eq)):
                    if comp_str == "None" and left_str:
                        guarded.add(left_str)
                    elif left_str == "None" and comp_str:
                        guarded.add(comp_str)
        elif isinstance(child, ast.Assign):
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


def _extract_dunder_all(tree: ast.Module) -> set[str] | None:
    """Extract explicitly declared `__all__` export names if `__all__` is defined in the module."""
    exported: set[str] | None = None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__all__":
                    if exported is None:
                        exported = set()
                    if isinstance(node.value, (ast.List, ast.Tuple, ast.Set)):
                        for elt in node.value.elts:
                            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                exported.add(elt.value)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == "__all__" and node.value is not None:
                if exported is None:
                    exported = set()
                if isinstance(node.value, (ast.List, ast.Tuple, ast.Set)):
                    for elt in node.value.elts:
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                            exported.add(elt.value)
        elif isinstance(node, ast.AugAssign):
            if isinstance(node.target, ast.Name) and node.target.id == "__all__":
                if exported is None:
                    exported = set()
                if isinstance(node.value, (ast.List, ast.Tuple, ast.Set)):
                    for elt in node.value.elts:
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                            exported.add(elt.value)
    return exported


def _determine_py_export_exposure(
    top_symbol_name: str,
    file_path: str,
    dunder_all: set[str] | None,
    init_reexports: set[str] | None = None,
) -> tuple[bool, str]:
    """Determine if a Python symbol is part of the public API surface exposed to external consumers."""
    norm_path = file_path.replace("\\", "/")
    parts = [p for p in norm_path.split("/") if p]

    if dunder_all is not None:
        if top_symbol_name in dunder_all:
            return True, f"Explicitly exported in `__all__` of `{norm_path}`"
        return False, f"Omitted from explicit `__all__` in `{norm_path}`"

    if init_reexports and top_symbol_name in init_reexports:
        return True, "Re-exported in package root `__init__.py`"

    if norm_path.endswith("__init__.py"):
        return True, f"Defined in package entrypoint `{norm_path}`"

    is_private_path = any(
        (seg.startswith("_") and seg != "__init__.py") or seg in ("internal", "_internal", "tests", "test")
        for seg in parts
    )
    if is_private_path:
        return False, f"Internal/private module path `{norm_path}`"

    return True, f"Public top-level symbol in module `{norm_path}`"


def _is_enum_class(bases: list[str]) -> bool:
    for b in bases:
        short_b = b.split(".")[-1]
        if short_b in _ENUM_BASE_NAMES:
            return True
    return False


def _parse_python_symbols(
    source: str,
    file_path: str = "module.py",
    init_reexports: set[str] | None = None,
) -> dict[str, _PySymbolInfo]:
    """Parse public Python functions, classes, methods, enums, and constants from source code."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}

    dunder_all = _extract_dunder_all(tree)
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
            is_pub, pub_reason = _determine_py_export_exposure(node.name, file_path, dunder_all, init_reexports)
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
                body_ast_dump=_compute_body_ast_dump(node),
                is_public_export=is_pub,
                export_reason=pub_reason,
            )
        elif isinstance(node, ast.ClassDef):
            if node.name.startswith("_"):
                continue
            bases = [b for b in (_unparse_safe(base) for base in node.bases) if b]
            decs = [d for d in (_unparse_safe(dec) for dec in node.decorator_list) if d]
            base_sig = f"({', '.join(bases)})" if bases else ""
            dec_prefix = "".join(f"@{d} " for d in decs) if decs else ""
            is_enum = _is_enum_class(bases)
            is_pub, pub_reason = _determine_py_export_exposure(node.name, file_path, dunder_all, init_reexports)

            enum_members: dict[str, str] = {}
            if is_enum:
                for item in node.body:
                    if isinstance(item, ast.Assign):
                        for t in item.targets:
                            if isinstance(t, ast.Name) and not t.id.startswith("_"):
                                enum_members[t.id] = _unparse_safe(item.value) or ""
                    elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                        if not item.target.id.startswith("_") and item.value is not None:
                            enum_members[item.target.id] = _unparse_safe(item.value) or ""

            cls_sig = f"{dec_prefix}class {node.name}{base_sig}"
            if is_enum and enum_members:
                members_preview = ", ".join(f"{k}={v}" for k, v in list(enum_members.items())[:8])
                cls_sig = f"{cls_sig} [{members_preview}]"

            symbols[node.name] = _PySymbolInfo(
                qualified_name=node.name,
                short_name=node.name,
                symbol_kind="enum" if is_enum else "class",
                start_line=getattr(node, "lineno", 1),
                end_line=getattr(node, "end_lineno", getattr(node, "lineno", 1)),
                signature_str=cls_sig,
                decorators=decs,
                bases=bases,
                enum_members=enum_members,
                is_public_export=is_pub,
                export_reason=pub_reason,
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
                    m_body_dump = _compute_body_ast_dump(item)

                    is_setter_or_deleter = any(d.endswith((".setter", ".deleter")) for d in m_decs)
                    if qname in symbols and is_setter_or_deleter:
                        qname_sub = (
                            f"{qname}.setter"
                            if any(d.endswith(".setter") for d in m_decs)
                            else f"{qname}.deleter"
                        )
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
                            body_ast_dump=m_body_dump,
                            is_public_export=is_pub,
                            export_reason=pub_reason,
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
                        body_ast_dump=m_body_dump,
                        is_public_export=is_pub,
                        export_reason=pub_reason,
                    )
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if (
                    isinstance(t, ast.Name)
                    and len(t.id) >= 2
                    and t.id.isupper()
                    and not t.id.startswith("_")
                ):
                    val_str = _unparse_safe(node.value) or "..."
                    is_pub, pub_reason = _determine_py_export_exposure(t.id, file_path, dunder_all, init_reexports)
                    symbols[t.id] = _PySymbolInfo(
                        qualified_name=t.id,
                        short_name=t.id,
                        symbol_kind="constant",
                        start_line=getattr(node, "lineno", 1),
                        end_line=getattr(node, "end_lineno", getattr(node, "lineno", 1)),
                        signature_str=f"{t.id} = {val_str}",
                        is_public_export=is_pub,
                        export_reason=pub_reason,
                    )
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            t_id = node.target.id
            if len(t_id) >= 2 and t_id.isupper() and not t_id.startswith("_"):
                ann_str = _unparse_safe(node.annotation) or ""
                val_str = _unparse_safe(node.value) if node.value is not None else None
                sig_c = f"{t_id}: {ann_str}" + (f" = {val_str}" if val_str is not None else "")
                is_pub, pub_reason = _determine_py_export_exposure(t_id, file_path, dunder_all, init_reexports)
                symbols[t_id] = _PySymbolInfo(
                    qualified_name=t_id,
                    short_name=t_id,
                    symbol_kind="constant",
                    start_line=getattr(node, "lineno", 1),
                    end_line=getattr(node, "end_lineno", getattr(node, "lineno", 1)),
                    signature_str=sig_c,
                    is_public_export=is_pub,
                    export_reason=pub_reason,
                )

    return symbols


def _compare_python_symbol(
    file_path: str,
    old_sym: _PySymbolInfo,
    new_sym: _PySymbolInfo,
) -> list[tuple[str, bool, str, str]]:
    """Return list of (change_kind, is_breaking_candidate, severity, details) for a symbol pair."""
    findings: list[tuple[str, bool, str, str]] = []

    if old_sym.symbol_kind == "constant" and new_sym.symbol_kind == "constant":
        if old_sym.signature_str != new_sym.signature_str:
            findings.append(
                (
                    "signature_changed",
                    True,
                    "medium",
                    f"Public constant '{old_sym.qualified_name}' changed from `{old_sym.signature_str}` to `{new_sym.signature_str}`.",
                )
            )
        return findings

    if old_sym.symbol_kind in ("class", "enum") and new_sym.symbol_kind in ("class", "enum"):
        if old_sym.bases != new_sym.bases:
            old_base_short = {b.split(".")[-1] for b in old_sym.bases}
            new_base_short = {b.split(".")[-1] for b in new_sym.bases}
            is_strenum_migration = (
                ("str" in old_base_short and "Enum" in old_base_short and "StrEnum" in new_base_short)
                or ("StrEnum" in old_base_short and "str" in new_base_short and "Enum" in new_base_short)
            )
            if is_strenum_migration:
                findings.append(
                    (
                        "base_class_changed",
                        True,
                        "high",
                        f"Enum '{old_sym.qualified_name}' base classes changed from {old_sym.bases} to {new_sym.bases} (griffe#501: str(member) formatting semantics change between (str, Enum) and StrEnum).",
                    )
                )
            else:
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
        if old_sym.enum_members or new_sym.enum_members:
            removed_members = [m for m in old_sym.enum_members if m not in new_sym.enum_members]
            if removed_members:
                findings.append(
                    (
                        "signature_changed",
                        True,
                        "high",
                        f"Removed Enum member(s) {removed_members} from '{old_sym.qualified_name}'.",
                    )
                )
            changed_vals = [
                f"{m} ({old_sym.enum_members[m]} -> {new_sym.enum_members[m]})"
                for m in old_sym.enum_members
                if m in new_sym.enum_members and old_sym.enum_members[m] != new_sym.enum_members[m]
            ]
            if changed_vals:
                findings.append(
                    (
                        "signature_changed",
                        True,
                        "medium",
                        f"Enum member value(s) changed in '{old_sym.qualified_name}': {', '.join(changed_vals)}.",
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
        p.name
        for p in new_sym.params
        if p.name not in old_by_name and not p.has_default and p.kind not in ("VAR_POSITIONAL", "VAR_KEYWORD")
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
        p.name
        for p in new_sym.params
        if p.name not in old_by_name and p.has_default and p.kind not in ("VAR_POSITIONAL", "VAR_KEYWORD")
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
        elif old_p.has_default and new_p.has_default and old_p.default_repr != new_p.default_repr:
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


def _check_call_arity_mismatch(
    call_node: ast.Call,
    target_sym: _PySymbolInfo | None,
    change_kind: str,
) -> tuple[bool, str]:
    """Verify whether an AST call site is incompatible with the target symbol's updated signature."""
    if change_kind == "removed":
        return True, "Symbol was removed in target revision; call site will raise NameError or AttributeError."
    if target_sym is None or target_sym.symbol_kind not in ("function", "async_function", "method"):
        return False, ""

    has_starred = any(isinstance(a, ast.Starred) for a in call_node.args)
    has_kwargs_unpack = any(kw.arg is None for kw in call_node.keywords)
    pos_passed = len(call_node.args)
    kw_passed = {kw.arg for kw in call_node.keywords if kw.arg is not None}

    pos_params = [p for p in target_sym.params if p.kind in ("POSITIONAL_ONLY", "POSITIONAL_OR_KEYWORD")]
    kw_only_params = [p for p in target_sym.params if p.kind == "KEYWORD_ONLY"]
    has_varargs = any(p.kind == "VAR_POSITIONAL" for p in target_sym.params)
    has_varkw = any(p.kind == "VAR_KEYWORD" for p in target_sym.params)

    # Check too many positional arguments
    if not has_varargs and not has_starred and pos_passed > len(pos_params):
        return (
            True,
            f"Call passes {pos_passed} positional argument(s), but updated signature accepts at most {len(pos_params)}.",
        )

    # Check unknown keyword arguments
    if not has_varkw and kw_passed:
        valid_kw_names = {
            p.name for p in target_sym.params if p.kind in ("POSITIONAL_OR_KEYWORD", "KEYWORD_ONLY")
        }
        invalid_kw = sorted(kw_passed - valid_kw_names)
        if invalid_kw:
            return (
                True,
                f"Call passes unsupported/removed keyword argument(s) {invalid_kw} not in updated signature.",
            )

    # Check missing required parameters
    if not has_starred and not has_kwargs_unpack:
        missing_req: list[str] = []
        for idx, p in enumerate(pos_params):
            if not p.has_default:
                satisfied_by_pos = idx < pos_passed
                satisfied_by_kw = p.kind == "POSITIONAL_OR_KEYWORD" and p.name in kw_passed
                if not (satisfied_by_pos or satisfied_by_kw):
                    missing_req.append(p.name)
        for p in kw_only_params:
            if not p.has_default and p.name not in kw_passed:
                missing_req.append(p.name)
        if missing_req:
            return (
                True,
                f"Call passes {pos_passed} positional arg(s) and keywords {sorted(kw_passed)}, missing required parameter(s) {missing_req}.",
            )

    return False, ""


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


def _build_line_to_enclosing_symbol(tree: ast.AST) -> dict[int, str]:
    """Map source line numbers inside a Python AST to their enclosing function or class name."""
    line_map: dict[int, str] = {}
    for node in getattr(tree, "body", []):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            start = getattr(node, "lineno", 1)
            end = getattr(node, "end_lineno", start)
            for ln in range(start, end + 1):
                line_map[ln] = node.name
        elif isinstance(node, ast.ClassDef):
            c_start = getattr(node, "lineno", 1)
            c_end = getattr(node, "end_lineno", c_start)
            for ln in range(c_start, c_end + 1):
                line_map[ln] = node.name
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    m_start = getattr(item, "lineno", 1)
                    m_end = getattr(item, "end_lineno", m_start)
                    for ln in range(m_start, m_end + 1):
                        line_map[ln] = f"{node.name}.{item.name}"
    return line_map


def _detect_py_dynamic_calls(
    tree: ast.AST,
    lines: list[str],
    rel_path: str,
    line_map: dict[int, str],
    breaking_short_names: dict[str, str],
) -> list[UnresolvedDynamicCallItem]:
    """Detect dynamic dispatch, reflection (getattr), globals/locals lookups, and eval/exec in Python AST."""
    dynamic_items: list[UnresolvedDynamicCallItem] = []
    seen_lines: set[int] = set()

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        lineno = getattr(node, "lineno", 1)
        if lineno in seen_lines:
            continue
        snippet = lines[lineno - 1].strip()[:160] if 1 <= lineno <= len(lines) else ""
        enclosing = line_map.get(lineno)

        # 1. getattr(obj, attr) / hasattr(obj, attr)
        if isinstance(node.func, ast.Name) and node.func.id in ("getattr", "hasattr", "setattr"):
            if len(node.args) >= 2:
                second_arg = node.args[1]
                if isinstance(second_arg, ast.Constant) and isinstance(second_arg.value, str):
                    attr_str = second_arg.value
                    if attr_str in breaking_short_names:
                        seen_lines.add(lineno)
                        dynamic_items.append(
                            UnresolvedDynamicCallItem(
                                file_path=rel_path,
                                line_number=lineno,
                                enclosing_symbol=enclosing,
                                pattern_kind="getattr_reflection",
                                snippet=snippet,
                                referenced_symbol_hint=breaking_short_names[attr_str],
                                risk_explanation=(
                                    f"Reflective `{node.func.id}` lookup explicitly references modified symbol "
                                    f"'{attr_str}' at `{rel_path}:{lineno}`."
                                ),
                            )
                        )
                else:
                    seen_lines.add(lineno)
                    dynamic_items.append(
                        UnresolvedDynamicCallItem(
                            file_path=rel_path,
                            line_number=lineno,
                            enclosing_symbol=enclosing,
                            pattern_kind="getattr_reflection",
                            snippet=snippet,
                            referenced_symbol_hint=None,
                            risk_explanation=(
                                f"Dynamic `{node.func.id}` reflection with non-literal attribute name at "
                                f"`{rel_path}:{lineno}` cannot be statically resolved to a fixed symbol."
                            ),
                        )
                    )

        # 2. eval(...) / exec(...)
        elif isinstance(node.func, ast.Name) and node.func.id in ("eval", "exec", "__import__"):
            seen_lines.add(lineno)
            kind = "importlib_dynamic" if node.func.id == "__import__" else "eval_exec_dynamic"
            dynamic_items.append(
                UnresolvedDynamicCallItem(
                    file_path=rel_path,
                    line_number=lineno,
                    enclosing_symbol=enclosing,
                    pattern_kind=kind,  # type: ignore[arg-type]
                    snippet=snippet,
                    referenced_symbol_hint=None,
                    risk_explanation=(
                        f"Dynamic `{node.func.id}(...)` execution at `{rel_path}:{lineno}` may invoke modified symbols at runtime."
                    ),
                )
            )

        # 3. importlib.import_module(...)
        elif (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "import_module"
        ):
            seen_lines.add(lineno)
            dynamic_items.append(
                UnresolvedDynamicCallItem(
                    file_path=rel_path,
                    line_number=lineno,
                    enclosing_symbol=enclosing,
                    pattern_kind="importlib_dynamic",
                    snippet=snippet,
                    referenced_symbol_hint=None,
                    risk_explanation=(
                        f"Dynamic module import `import_module(...)` at `{rel_path}:{lineno}` may load modified exports at runtime."
                    ),
                )
            )

        # 4. globals() / locals() namespace lookup (e.g. fn = globals()[name] or globals().get(name))
        elif isinstance(node.func, ast.Name) and node.func.id in ("globals", "locals"):
            seen_lines.add(lineno)
            dynamic_items.append(
                UnresolvedDynamicCallItem(
                    file_path=rel_path,
                    line_number=lineno,
                    enclosing_symbol=enclosing,
                    pattern_kind="globals_locals_lookup",
                    snippet=snippet,
                    referenced_symbol_hint=None,
                    risk_explanation=(
                        f"Dynamic `{node.func.id}()` namespace lookup at `{rel_path}:{lineno}` "
                        f"bypasses static call graph resolution."
                    ),
                )
            )

        # 5. globals()[name](...) or locals()[name](...) or obj.__dict__[name](...)
        elif isinstance(node.func, ast.Subscript):
            sub_val_str = _unparse_safe(node.func.value) or ""
            if any(tok in sub_val_str for tok in ("globals()", "locals()", "__dict__", "registry", "handlers", "dispatch")):
                seen_lines.add(lineno)
                hint: str | None = None
                slice_str = _unparse_safe(node.func.slice) or ""
                cleaned_slice = slice_str.strip("'\"")
                if cleaned_slice in breaking_short_names:
                    hint = breaking_short_names[cleaned_slice]
                dynamic_items.append(
                    UnresolvedDynamicCallItem(
                        file_path=rel_path,
                        line_number=lineno,
                        enclosing_symbol=enclosing,
                        pattern_kind="globals_locals_lookup",
                        snippet=snippet,
                        referenced_symbol_hint=hint,
                        risk_explanation=(
                            f"Dynamic dictionary/namespace dispatch `{sub_val_str}[{slice_str}](...)` at "
                            f"`{rel_path}:{lineno}` bypasses static call graph resolution."
                        ),
                    )
                )

    return dynamic_items


_TS_DYNAMIC_DISPATCH_RE = re.compile(
    r"(?:\b(?:eval|Reflect\.apply)\s*\(|[A-Za-z0-9_$)\]]+\[\s*[A-Za-z_$][A-Za-z0-9_$.]*\s*\]\s*\()"
)


def _count_call_args_in_text(line: str, func_name: str) -> int | None:
    """Extract argument count from a text call site `func_name(a, b)` in Markdown or TS."""
    m = re.search(rf"\b{re.escape(func_name)}\s*\(([^)]*)\)", line)
    if not m:
        return None
    inner = m.group(1).strip()
    if not inner:
        return 0
    # Split top-level commas ignoring nested parens/brackets
    depth = 0
    count = 1
    for ch in inner:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth = max(0, depth - 1)
        elif ch == "," and depth == 0:
            count += 1
    return count


def _scan_callers_and_tests_and_docs(
    extraction: RawRangeExtraction,
    symbol_changes: list[SymbolSignatureChange],
    modified_symbol_names: set[str],
    new_py_symbols_by_qname: dict[str, _PySymbolInfo] | None = None,
) -> tuple[
    dict[str, list[CallerImpactItem]],
    list[str],
    list[DocDriftItem],
    list[UnresolvedDynamicCallItem],
    AnalysisCoverageReport,
]:
    """Scan repository files for call sites of breaking symbols, arity mismatches, dynamic calls, test coverage, and doc drift."""
    repo_path = extraction.repo_path
    target_sha = extraction.resolved_target_sha
    all_files = _list_repo_files_at_target(repo_path, target_sha)
    new_py_map = new_py_symbols_by_qname or {}

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

    caller_impacts: dict[str, list[CallerImpactItem]] = {
        sc.symbol_name: [] for sc in symbol_changes if sc.is_breaking_candidate
    }
    tested_symbols: set[str] = set()
    doc_drift_items: list[DocDriftItem] = []
    unresolved_dynamic_calls: list[UnresolvedDynamicCallItem] = []

    changed_doc_paths = {f.file_path for f in extraction.changed_files if f.is_doc_file}

    total_code_eligible = 0
    files_parsed_ok = 0
    files_skipped_or_err = 0

    eligible_scan_files = [
        f for f in all_files if f.endswith((".py", ".ts", ".tsx", ".js", ".jsx", ".md", ".rst"))
    ]
    max_scan_files = 1000

    for rel_path in eligible_scan_files[:max_scan_files]:
        is_py = rel_path.endswith(".py")
        is_ts = rel_path.endswith((".ts", ".tsx", ".js", ".jsx"))
        is_md = rel_path.endswith((".md", ".rst"))

        if is_py or is_ts:
            total_code_eligible += 1

        # Read file content from target_blobs cache, working tree, or target git revision
        content = extraction.target_blobs.get(rel_path)
        if content is None:
            if target_sha == "WORKING_TREE":
                candidate = repo_path / rel_path
                if candidate.exists() and candidate.is_file() and is_safe_repo_file(repo_path, candidate):
                    try:
                        if candidate.stat().st_size <= 256 * 1024:
                            content = candidate.read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        content = None
            else:
                # Try working tree fast-path first if file was not modified since target_sha, fallback to git cat-file/show
                candidate = repo_path / rel_path
                if candidate.exists() and candidate.is_file() and is_safe_repo_file(repo_path, candidate):
                    try:
                        if candidate.stat().st_size <= 256 * 1024:
                            content = candidate.read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        content = None
                if content is None:
                    content = read_blob_at_revision(repo_path, target_sha, rel_path)

        if content is None:
            if is_py or is_ts:
                files_skipped_or_err += 1
            continue

        is_test = _is_test_file(rel_path)
        if is_test:
            for sym_qname in modified_symbol_names:
                short_n = sym_qname.split(".")[-1]
                if short_n in ("__init__", "__call__"):
                    short_n = sym_qname.split(".")[0]
                if re.search(rf"\b{re.escape(short_n)}\b", content):
                    tested_symbols.add(sym_qname)

        if is_py:
            try:
                tree = ast.parse(content)
                files_parsed_ok += 1
                lines = content.splitlines()
                line_map = _build_line_to_enclosing_symbol(tree)

                # Scan for dynamic dispatch / reflection calls in non-test files
                if not is_test and len(unresolved_dynamic_calls) < 25:
                    dyn_found = _detect_py_dynamic_calls(
                        tree, lines, rel_path, line_map, breaking_short_names
                    )
                    unresolved_dynamic_calls.extend(dyn_found[: 25 - len(unresolved_dynamic_calls)])

                if breaking_short_names:
                    file_imports = _extract_py_imports(tree)
                    for node in ast.walk(tree):
                        if isinstance(node, ast.Call):
                            callee_name: str | None = None
                            is_attr_call = False
                            if isinstance(node.func, ast.Name):
                                callee_name = node.func.id
                            elif isinstance(node.func, ast.Attribute):
                                callee_name = node.func.attr
                                is_attr_call = True
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
                                lineno = getattr(node, "lineno", 1)
                                if (
                                    rel_path == sc_obj.file_path
                                    and sc_obj.target_line_span
                                    and sc_obj.target_line_span.start_line <= lineno <= sc_obj.target_line_span.end_line
                                ):
                                    continue
                                snippet = lines[lineno - 1].strip() if 1 <= lineno <= len(lines) else callee_name
                                def_mod_stem = Path(sc_obj.file_path).stem
                                has_explicit_import = (callee_name in file_imports) or any(
                                    def_mod_stem in v for v in file_imports.values()
                                )
                                # If calling .method() on an arbitrary receiver without importing the module, classify as heuristic
                                if rel_path == sc_obj.file_path or (has_explicit_import and not is_attr_call):
                                    conf_level = "exact_ast"
                                elif has_explicit_import and is_attr_call:
                                    conf_level = "exact_ast"
                                else:
                                    conf_level = "heuristic_name_match"

                                arity_broken, mismatch_msg = _check_call_arity_mismatch(
                                    node,
                                    new_py_map.get(qname),
                                    sc_obj.change_kind,
                                )
                                enclosing = line_map.get(lineno)
                                if len(caller_impacts.get(qname, [])) < 10:
                                    caller_impacts.setdefault(qname, []).append(
                                        CallerImpactItem(
                                            caller_file=rel_path,
                                            caller_symbol=callee_name or target_short_name,
                                            enclosing_symbol=enclosing,
                                            line_number=lineno,
                                            call_snippet=snippet[:160],
                                            resolution_confidence=conf_level,
                                            arity_mismatch_detected=arity_broken,
                                            mismatch_details=mismatch_msg,
                                        )
                                    )
            except SyntaxError:
                files_skipped_or_err += 1

        elif is_ts:
            files_parsed_ok += 1
            lines = content.splitlines()
            if not is_test and len(unresolved_dynamic_calls) < 25:
                for idx, line in enumerate(lines, start=1):
                    if _TS_DYNAMIC_DISPATCH_RE.search(line):
                        unresolved_dynamic_calls.append(
                            UnresolvedDynamicCallItem(
                                file_path=rel_path,
                                line_number=idx,
                                enclosing_symbol=None,
                                pattern_kind="ts_bracket_dispatch",
                                snippet=line.strip()[:160],
                                referenced_symbol_hint=None,
                                risk_explanation=(
                                    f"Dynamic property/reflection invocation in TypeScript/JS at `{rel_path}:{idx}` "
                                    "cannot be statically bound to a fixed export."
                                ),
                            )
                        )
                        if len(unresolved_dynamic_calls) >= 25:
                            break

            if breaking_short_names:
                for short_n, qname in breaking_short_names.items():
                    sc_obj = breaking_by_qname[qname]
                    for idx, line in enumerate(lines, start=1):
                        if (
                            rel_path == sc_obj.file_path
                            and sc_obj.target_line_span
                            and sc_obj.target_line_span.start_line == idx
                        ):
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
            lines = content.splitlines()
            for short_n, qname in breaking_short_names.items():
                sc_obj = breaking_by_qname[qname]
                target_py_sym = new_py_map.get(qname)
                min_req_args = (
                    sum(
                        1
                        for p in target_py_sym.params
                        if not p.has_default and p.kind not in ("VAR_POSITIONAL", "VAR_KEYWORD")
                    )
                    if target_py_sym
                    else None
                )
                for idx, line in enumerate(lines, start=1):
                    has_call = bool(re.search(rf"\b{re.escape(short_n)}\s*\(", line))
                    has_backtick = f"`{short_n}`" in line or f"`{qname}`" in line
                    if not (has_call or has_backtick):
                        continue

                    call_arg_cnt = _count_call_args_in_text(line, short_n) if has_call else None
                    arity_stale = (
                        call_arg_cnt is not None
                        and min_req_args is not None
                        and call_arg_cnt < min_req_args
                    )

                    # Flag if:
                    # 1. Symbol was completely removed, OR
                    # 2. Call in Markdown explicitly passes fewer arguments than the new required signature (even if doc file was edited elsewhere!), OR
                    # 3. Doc file was not modified in the commit range while symbol signature broke
                    if sc_obj.change_kind == "removed" or arity_stale or (rel_path not in changed_doc_paths):
                        ev_id = sc_obj.evidence_ids[0] if sc_obj.evidence_ids else None
                        arity_note = (
                            f" Example passes {call_arg_cnt} argument(s), but updated signature requires at least {min_req_args}."
                            if arity_stale
                            else ""
                        )
                        doc_drift_items.append(
                            DocDriftItem(
                                doc_file=rel_path,
                                line_number=idx,
                                referenced_symbol=qname,
                                snippet=line.strip()[:160],
                                drift_reason=(
                                    f"Documentation in '{rel_path}:{idx}' references '{short_n}', "
                                    f"whose signature/availability changed ({sc_obj.change_kind}: {sc_obj.details}).{arity_note}"
                                ),
                                evidence_ids=[ev_id] if ev_id else [],
                            )
                        )
                        break

    # Account for code files beyond the max_scan_files cap if repo is huge
    if len(eligible_scan_files) > max_scan_files:
        extra_code = sum(
            1
            for f in eligible_scan_files[max_scan_files:]
            if f.endswith((".py", ".ts", ".tsx", ".js", ".jsx"))
        )
        total_code_eligible += extra_code
        files_skipped_or_err += extra_code

    coverage_pct = (
        round((files_parsed_ok / total_code_eligible) * 100.0, 1)
        if total_code_eligible > 0
        else 100.0
    )

    all_caller_items = [c for clist in caller_impacts.values() for c in clist]
    exact_cnt = sum(1 for c in all_caller_items if c.resolution_confidence == "exact_ast")
    heur_cnt = sum(1 for c in all_caller_items if c.resolution_confidence == "heuristic_name_match")
    arity_cnt = sum(1 for c in all_caller_items if c.arity_mismatch_detected)
    dyn_cnt = len(unresolved_dynamic_calls)
    pub_brk_cnt = sum(
        1 for sc in symbol_changes if sc.is_breaking_candidate and sc.is_public_api_export
    )

    reasons: list[str] = []
    conf_rating: ConfidenceLevel = "high"
    if coverage_pct < 75.0:
        conf_rating = "low"
        reasons.append(
            f"Only {coverage_pct}% ({files_parsed_ok}/{total_code_eligible}) of eligible code files were statically parsed."
        )
    else:
        reasons.append(
            f"Statically parsed {coverage_pct}% ({files_parsed_ok}/{total_code_eligible}) of eligible repository source files."
        )

    if dyn_cnt > 0:
        if conf_rating == "high":
            conf_rating = "medium"
        reasons.append(
            f"Detected {dyn_cnt} unresolved dynamic/reflective invocation(s) (e.g., getattr/eval/dynamic dispatch) that sit outside static call graphs."
        )
    if heur_cnt > 0:
        if conf_rating == "high":
            conf_rating = "medium"
        reasons.append(
            f"{heur_cnt} caller reference(s) rely on heuristic name matching rather than proven import bindings."
        )
    if pub_brk_cnt > 0:
        if conf_rating == "high":
            conf_rating = "medium"
        reasons.append(
            f"{pub_brk_cnt} breaking symbol(s) are publicly exported APIs; external downstream consumers outside this repository may be impacted even if internal callers are clean."
        )
    if conf_rating == "high":
        reasons.append("Zero unresolved dynamic calls and zero unverified external public API breakages detected.")

    coverage_report = AnalysisCoverageReport(
        total_repo_files_discovered=len(all_files),
        total_code_files_eligible=total_code_eligible,
        files_parsed_successfully=files_parsed_ok,
        files_skipped_or_unparseable=files_skipped_or_err,
        static_parse_coverage_pct=coverage_pct,
        exact_ast_callers_count=exact_cnt,
        heuristic_callers_count=heur_cnt,
        callers_with_arity_mismatch_count=arity_cnt,
        unresolved_dynamic_calls_count=dyn_cnt,
        public_api_exports_modified_count=pub_brk_cnt,
        overall_blast_radius_confidence=conf_rating,
        confidence_rationale=" ".join(reasons),
    )

    untested = sorted(modified_symbol_names - tested_symbols)
    return caller_impacts, untested, doc_drift_items, unresolved_dynamic_calls, coverage_report


def _collect_init_reexports(extraction: RawRangeExtraction) -> set[str]:
    """Scan any `__init__.py` blobs in base/target or repo root for re-exported symbol names."""
    reexported: set[str] = set()
    for path_key, blob in {**extraction.base_blobs, **extraction.target_blobs}.items():
        if path_key.endswith("__init__.py") and blob:
            try:
                tree = ast.parse(blob)
                dunder = _extract_dunder_all(tree)
                if dunder:
                    reexported.update(dunder)
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom):
                        for alias in node.names:
                            reexported.add(alias.asname or alias.name)
            except SyntaxError:
                pass
    return reexported


def run_structural_code_analysis(extraction: RawRangeExtraction) -> StructuralAnalysisResult:
    """Run deterministic Python AST and TS/JS signature diffing, caller blast-radius scan, dynamic call scan, and doc-drift detection."""
    symbol_changes: list[SymbolSignatureChange] = []
    modified_symbol_names: set[str] = set()
    new_py_symbols_by_qname: dict[str, _PySymbolInfo] = {}
    init_reexports = _collect_init_reexports(extraction)

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
            old_syms = _parse_python_symbols(base_src, file_path=fpath, init_reexports=init_reexports) if base_src else {}
            new_syms = _parse_python_symbols(target_src, file_path=fpath, init_reexports=init_reexports) if target_src else {}
            new_py_symbols_by_qname.update(new_syms)

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
                        is_public_api_export=old_s.is_public_export,
                        public_api_exposure_reason=old_s.export_reason,
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
                        is_public_api_export=new_s.is_public_export,
                        public_api_exposure_reason=new_s.export_reason,
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
                # Track body-only mutations so untested_modified_symbols catches implementation changes without signature changes (BUG-018)
                if old_s.body_ast_dump != new_s.body_ast_dump:
                    modified_symbol_names.add(qname)
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
                    is_pub_exp = old_s.is_public_export or new_s.is_public_export
                    pub_exp_reason = new_s.export_reason or old_s.export_reason
                    sc = SymbolSignatureChange(
                        file_path=fpath,
                        symbol_name=qname,
                        symbol_kind=new_s.symbol_kind,  # type: ignore[arg-type]
                        change_kind=change_kind,  # type: ignore[arg-type]
                        old_signature=old_s.signature_str,
                        new_signature=new_s.signature_str,
                        is_breaking_candidate=is_brk,
                        is_public_api_export=is_pub_exp,
                        public_api_exposure_reason=pub_exp_reason,
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
            ts_export_reason = (
                f"Exported from package entrypoint `{fpath}`"
                if Path(fpath).stem == "index"
                else f"Explicitly exported with `export` keyword in `{fpath}`"
            )
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
                        is_public_api_export=True,
                        public_api_exposure_reason=ts_export_reason,
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
                        old_req = [p for p in old_params if "=" not in p and "?" not in p]
                        new_req = [p for p in new_params if "=" not in p and "?" not in p]
                        is_brk = (
                            len(new_req) > len(old_req)
                            or len(new_params) < len(old_params)
                            or (old_ret != new_ret and old_ret is not None)
                        )
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
                            is_public_api_export=True,
                            public_api_exposure_reason=ts_export_reason,
                            severity="high" if is_brk else "low",
                            details=reason,
                            base_line_span=b_span,
                            target_line_span=t_span,
                            evidence_ids=[eid],
                        )
                        symbol_changes.append(sc)
                        fdelta.symbol_changes.append(sc)
                        modified_symbol_names.add(name)

    (
        caller_impacts,
        untested_symbols,
        doc_drift_items,
        unresolved_dynamic_calls,
        coverage_report,
    ) = _scan_callers_and_tests_and_docs(
        extraction,
        symbol_changes,
        modified_symbol_names,
        new_py_symbols_by_qname=new_py_symbols_by_qname,
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
        related_dynamic = [
            d
            for d in unresolved_dynamic_calls
            if d.referenced_symbol_hint in (None, sc.symbol_name)
        ]

        observed_facts = [
            f"In '{sc.file_path}', symbol '{sc.symbol_name}' ({sc.symbol_kind}) underwent '{sc.change_kind}'.",
            sc.details,
        ]
        if sc.old_signature:
            observed_facts.append(f"Base signature: `{sc.old_signature}`")
        if sc.new_signature:
            observed_facts.append(f"Target signature: `{sc.new_signature}`")
        if sc.is_public_api_export:
            observed_facts.append(
                f"Public API Export Exposure: `{sc.symbol_name}` is publicly exported ({sc.public_api_exposure_reason})."
            )
        if callers:
            broken_callers = [c for c in callers if c.arity_mismatch_detected]
            caller_summary = (
                f"Detected {len(callers)} downstream call site(s) in repository "
                f"({len(broken_callers)} with proven argument/arity incompatibility: "
                f"{', '.join(f'{c.caller_file}:{c.line_number}' for c in callers[:3])})."
            )
            observed_facts.append(caller_summary)
        if related_drift:
            observed_facts.append(
                f"Detected {len(related_drift)} potentially stale documentation reference(s) (e.g., {', '.join(f'{d.doc_file}:{d.line_number}' for d in related_drift[:2])})."
            )
        if related_dynamic:
            observed_facts.append(
                f"Detected {len(related_dynamic)} unresolved dynamic/reflective invocation(s) in repository (e.g., {', '.join(f'{d.file_path}:{d.line_number}' for d in related_dynamic[:2])})."
            )

        risks = [
            "Requires human review: External callers or plugins invoking the previous signature may fail at runtime with TypeError, AttributeError, or unexpected behavior.",
        ]
        if sc.is_public_api_export:
            risks.append(
                f"[PUBLIC API EXPORT] `{sc.symbol_name}` is part of the public API surface ({sc.public_api_exposure_reason}): external downstream consumers outside this repository may break even when internal callers are updated."
            )
        if related_dynamic:
            risks.append(
                f"[UNRESOLVED DYNAMIC CALLS] {len(related_dynamic)} dynamic/reflective call site(s) (getattr/eval/dispatch) exist in the repository that static AST analysis cannot rule out."
            )
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
                is_public_api_export=sc.is_public_api_export,
                public_api_exposure_reason=sc.public_api_exposure_reason,
                observed_facts=observed_facts,
                model_interpretation=(
                    f"Deterministic AST analysis flagged `{sc.symbol_name}` in `{sc.file_path}` as a potential breaking change ({sc.details}). "
                    "Review callers, public export boundaries, and migration notes before release."
                ),
                potential_risks_requiring_verification=risks,
                affected_callers=callers,
                unresolved_dynamic_calls=related_dynamic[:5],
                doc_drift=related_drift,
                evidence_ids=list(dict.fromkeys(sc.evidence_ids)),
                evidence=[
                    extraction.evidence_registry[eid]
                    for eid in dict.fromkeys(sc.evidence_ids)
                    if eid in extraction.evidence_registry
                ],
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
        unresolved_dynamic_calls=unresolved_dynamic_calls,
        analysis_coverage=coverage_report,
        deterministic_breaking_findings=breaking_findings,
    )


def analyze_python_module_diff(
    old_code: str,
    new_code: str,
    file_path: str = "module.py",
) -> StructuralAnalysisResult:
    """Analyze changes between two versions of a Python source file directly."""
    old_syms = _parse_python_symbols(old_code, file_path=file_path) if old_code else {}
    new_syms = _parse_python_symbols(new_code, file_path=file_path) if new_code else {}

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
                is_public_api_export=old_s.is_public_export,
                public_api_exposure_reason=old_s.export_reason,
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
                is_public_api_export=new_s.is_public_export,
                public_api_exposure_reason=new_s.export_reason,
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
                is_public_api_export=old_s.is_public_export or new_s.is_public_export,
                public_api_exposure_reason=new_s.export_reason or old_s.export_reason,
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
            line_map = _build_line_to_enclosing_symbol(tree)
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
                                enclosing_symbol=line_map.get(line_no),
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
