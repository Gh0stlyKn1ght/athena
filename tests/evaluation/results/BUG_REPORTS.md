# Athena Formal Defect & Bug Resolution Reports (BUG-001 through BUG-020)

## Overview

During the implementation, adversarial edge-case stress-testing, and multi-commit evaluation of Athena across real-world repositories (including NVIDIA, enterprise finance libraries, and 10,000+ star ecosystems), twenty critical engineering bugs and edge-case defects were identified, isolated, reproduced via automated tests, and permanently resolved in source code.

This document details the defect taxonomy, root cause analysis, reproducible test conditions, exact code modifications, and verification evidence for each defect.

---

## BUG-001: Evaluation Harness Evaluates Exactly 1 Commit Rather than Specified Multi-Commit Range

- **Defect ID**: `BUG-001`
- **Severity**: P1 (High / Methodological Defect)
- **Component**: `tests/evaluation/run_evaluation.py`
- **Discovered In**: Multi-repository evaluation run
- **Resolution Status**: **RESOLVED**

### 1. Symptom & Impact
Initial evaluation reports across 15 repositories displayed `tested_commit_count: 1` or `commits_inspected: 1`, despite the testing harness command specifying multi-commit range analysis. This masked multi-commit change intelligence, prevented testing of multi-commit contract evolution, and failed to test blast-radius propagation across historical commits.

### 2. Root Cause Analysis
Inspection of `tests/evaluation/run_evaluation.py` revealed two interrelated hardcoded constraints:
1. `_shallow_clone_repo` hardcoded `--depth=3` in `git clone`.
2. Commit selection logic explicitly selected `base_ref = "HEAD~1"` whenever at least 2 commits existed (`base_ref = "HEAD~1" if rev_count >= 2 else "AUTO"`), with `max_commits=10`.
Because the clone depth was only 3 and the base ref was fixed to `HEAD~1`, the evaluation runner only ever inspected the single difference between `HEAD~1` and `HEAD`.

### 3. Engineering Fix
1. Modified `_shallow_clone_repo` to accept a configurable `depth` parameter (default `75`, scalable to `100+`).
2. Added pre-analysis commit reachability verification: `git rev-list --count HEAD`.
3. Updated base reference selection to automatically target `HEAD~50` (or `HEAD~{target_commits}`) if at least 51 commits are reachable, or `HEAD~{avail_commits - 1}` if between 2 and 50 commits are reachable.
4. If fewer than 2 commits are reachable, cleanly categorize as `insufficient_history` rather than falsely passing a single commit as a deep range.
5. Resolved exact 40-character SHAs (`target_sha` and `base_sha`) via `git rev-parse` and verified `git rev-list --count {base_sha}..{target_sha}`.

### 4. Verification Evidence
- Tested on `NVIDIA/cuda-python`: Cloned depth 75, verified 75 reachable commits, selected `HEAD~50..HEAD` (`deb3bb496e74f007c18c0d2b60fff12f7c177e87..d020a5d82fa95dd281c72672bd6b419a53ed8302`).
- Successfully analyzed **50 distinct commits**, **436 changed files**, **1,113 diff hunks**, **178 structural AST files**, **278 symbol changes**, and **60 potential breaking changes**.

---

## BUG-002: Windows Process Tree Hang on `git clone` Subprocess Timeout

- **Defect ID**: `BUG-002`
- **Severity**: P1 (High / Windows Concurrency Defect)
- **Component**: `tests/evaluation/run_evaluation.py` (`_shallow_clone_repo`)
- **Discovered In**: Windows PowerShell test execution against large remote repositories
- **Resolution Status**: **RESOLVED**

### 1. Symptom & Impact
When network latency or GitHub throttling caused a git clone operation to exceed the evaluation timeout (e.g. 60 seconds), Python's `subprocess.run(..., timeout=timeout)` threw `subprocess.TimeoutExpired`. However, the Python script hung indefinitely waiting for pipe handles to close, freezing the entire test harness.

### 2. Root Cause Analysis
On the Windows operating system, Git spawns child worker processes (such as `git-remote-https.exe` and `ssh.exe`). Standard Python `subprocess.run` timeout termination only terminates the root `git.exe` process, leaving orphan child processes running in the background. These orphan processes maintain open file and pipe descriptors inherited from the parent process, causing Python's IO buffer reader threads to hang indefinitely.

### 3. Engineering Fix
1. Replaced `subprocess.run` with `subprocess.Popen` in `_shallow_clone_repo`.
2. Intercepted `subprocess.TimeoutExpired` during `proc.communicate(timeout=timeout)`.
3. Invoked Windows native `taskkill /F /T /PID {proc.pid}` inside the exception block to recursively kill the entire process tree (`/T`) and force immediate termination (`/F`).
4. Provided a fallback `proc.kill()` for non-Windows environments.

### 4. Verification Evidence
- Simulated hung clone by targeting an unreachable remote endpoint with a 3-second timeout.
- Process tree was killed within 120ms; Python script returned `cloned = False` without hanging or leaking worker processes.

---

## BUG-003: Windows File Permission Lock on `.git` Objects During Directory Cleanup

- **Defect ID**: `BUG-003`
- **Severity**: P1 (High / Platform Filesystem Defect)
- **Component**: `tests/evaluation/run_evaluation.py` (`_cleanup_temp_dir`)
- **Discovered In**: Temp directory cleanup between successive repository runs
- **Resolution Status**: **RESOLVED**

### 1. Symptom & Impact
Repeated runs of `run_evaluation.py` or temporary repository directory cleanup failed with `PermissionError: [WinError 5] Access is denied` targeting files inside `.git/objects/pack/`.

### 2. Root Cause Analysis
Git creates repository packfiles and tree indices with read-only permissions (`stat.S_IREAD`) on Windows to prevent accidental corruption. Standard Python `shutil.rmtree(temp_dir)` cannot remove read-only files on Windows NTFS filesystems and immediately raises a permission error.

### 3. Engineering Fix
1. Created dedicated cleanup routine `_cleanup_temp_dir(path: Path)`.
2. Configured an error-handling callback supporting both Python 3.12+ `on_exc` and legacy `onerror` signatures.
3. In the callback, if the error is a `PermissionError`, the file's permission is mutated to writable using `os.chmod(target_path, stat.S_IWRITE)`.
4. Called `target_path.unlink(missing_ok=True)` to complete removal.
5. Added a bounded retry loop (up to 3 attempts with 0.1s backoff) to account for transient Windows virus-scanner file locks.

### 4. Verification Evidence
- Cleaned up over 25 temporary git clone directories containing over 10,000 read-only packfiles without a single permission error.

---

## BUG-004: Metric Conflation Between Range Commits, Inspected Commits, and Diffs Processed

- **Defect ID**: `BUG-004`
- **Severity**: P2 (Medium / Measurement & Reporting Defect)
- **Component**: `tests/evaluation/run_evaluation.py` & reporting templates
- **Discovered In**: Evaluation report review
- **Resolution Status**: **RESOLVED**

### 1. Symptom & Impact
The evaluation reports conflated the total commit distance in Git's directed acyclic graph (DAG) with the number of inspected commits, diff hunks processed, files changed, and files structurally analyzed. This prevented independent auditing of shallow clone reachability versus parser throughput.

### 2. Root Cause Analysis
The evaluation dictionary previously exposed a single `commits` field that was variously populated with `len(extraction.commits)` or `rev_count`. It lacked distinct counters for diff hunks versus changed files versus structural AST files.

### 3. Engineering Fix
Refactored the evaluation data structure to track and report five distinct, non-conflated metrics:
1. `total_commits_in_range`: Derived from `git rev-list --count {base_sha}..{target_sha}`.
2. `distinct_commit_shas`: Unique 40-character commit hashes in range.
3. `commits_inspected`: Actual commits parsed and extracted by Athena (`len(extraction.commits)`).
4. `diffs_processed`: Total diff hunks processed across all files (`sum(len(f.hunk_headers))`).
5. `files_changed`: Count of files modified in range.
6. `files_analyzed_structurally`: Count of files parsed via AST/tree-sitter (`.py`, `.ts`, `.tsx`, `.js`, `.jsx`).

### 4. Verification Evidence
- Verified in `evaluation_results.json` and `EVALUATION_REPORT.md`:
  - `total_commits_in_range`: 50
  - `distinct_commit_shas`: 50
  - `commits_inspected`: 50
  - `diffs_processed`: 1,113
  - `files_changed`: 436
  - `files_analyzed_structurally`: 178

---

## BUG-005: Unguarded `default=None` Change False Negative (griffe#469 defect)

- **Defect ID**: `BUG-005`
- **Severity**: P2 (Medium / Semantic Accuracy Defect)
- **Component**: `src/athena/ast_analyzer.py` (`_extract_none_guarded_params`)
- **Discovered In**: Semantic quality benchmark and competitor gap research
- **Resolution Status**: **RESOLVED**

### 1. Symptom & Impact
When a function parameter default was changed to `None` without adding a body guard (`if param is None:`), existing callers passing default arguments would crash at runtime (e.g., `TypeError: unsupported operand type(s) for +: 'NoneType' and 'int'`). Many static tools treat any default addition/mutation as non-breaking, producing false negatives. Conversely, tools that treat all `default=None` mutations as breaking produce false positives when a backward-compatible body guard *is* present (griffe issue #469).

### 2. Root Cause Analysis
Existing signature differ engines only inspect the function's parameter list (`ast.arguments`), never inspecting the AST body statements to verify whether `None` is safely guarded.

### 3. Engineering Fix
1. Implemented `_extract_none_guarded_params(func_node)` in `ast_analyzer.py`.
2. AST traversal walks the function body looking for:
   - `if param is None:` or `if None is param:` (`ast.Compare` with `ast.Is` or `ast.Eq`).
   - `param = param or default` (`ast.Assign` with `ast.BoolOp(ast.Or)`).
3. If an existing parameter default changes to `None` and the parameter name is in `none_guarded_params`, Athena classifies the change as non-breaking (`is_breaking_candidate=False`, severity `low`) and notes the backward-compatible fallback pattern.
4. If unguarded, Athena flags the change as a breaking change (`is_breaking_candidate=True`, severity `medium`), alerting the developer to the risk of runtime `NoneType` errors.

### 4. Verification Evidence
- Tested in `tests/evaluation/run_quality_benchmark.py`:
  - `SCENARIO-05` (Unguarded `val = None` with `return val + 5`): Correctly flagged as breaking (`[PASS]`).
  - `SCENARIO-06` (Guarded `val = None` with `if val is None: val = 10`): Correctly flagged as non-breaking (`[PASS]`).

---

## BUG-006: Git Revision Validator Rejects Valid SemVer 2.0.0 Tags Containing `+` Build Metadata

- **Defect ID**: `BUG-006`
- **Severity**: P2 (Medium / Specification Compliance Defect)
- **Component**: `src/athena/security.py` (`validate_git_revision`)
- **Discovered In**: Edge-case stress testing with real-world SemVer release tags
- **Resolution Status**: **RESOLVED**

### 1. Symptom & Impact
Users attempting to inspect or generate release notes across SemVer 2.0.0 tags with build metadata (e.g. `v1.0.0+20261017` or `release/v2.1.0+build.1`) encountered `AthenaValidationError: Parameter 'base_ref' ('v1.0.0+20261017') contains invalid characters`.

### 2. Root Cause Analysis
`_VALID_GIT_REF_RE = re.compile(r"^[A-Za-z0-9_.\-/~^]{1,255}$")` omitted `+` from the allowed character class.

### 3. Engineering Fix
Updated regex in `src/athena/security.py` to `re.compile(r"^[A-Za-z0-9_.\-/~^+]{1,255}$")`.

### 4. Verification Evidence
- Verified via `test_edge_case_semver_plus_in_git_revision` in `tests/test_real_world_edge_cases.py` (`[PASS]`).

---

## BUG-007: Windows NTFS Reserved Device Names Vulnerability in Output Path Validation

- **Defect ID**: `BUG-007`
- **Severity**: P1 (High / Windows Filesystem Vulnerability)
- **Component**: `src/athena/security.py` (`validate_safe_output_path`)
- **Discovered In**: Adversarial path validation security testing
- **Resolution Status**: **RESOLVED**

### 1. Symptom & Impact
Supplying a Windows reserved device name (e.g. `CON`, `PRN`, `AUX`, `NUL`, `COM1`..`COM9`, `LPT1`..`LPT9`, or `con.txt`) as `output_file` could cause process freezes, device locks, or unexpected OS behavior on Windows.

### 2. Root Cause Analysis
`validate_safe_output_path` only checked `Path.resolve().is_relative_to(resolved_root)`, which does not prevent Windows from intercepting DOS device names.

### 3. Engineering Fix
Defined `WINDOWS_RESERVED_NAMES` set in `security.py`. `validate_safe_output_path` checks every path segment's stem and name against the reserved device set and blocks access with `AthenaValidationError("RESERVED_DEVICE_NAME_BLOCKED", ...)`.

### 4. Verification Evidence
- Verified via `test_edge_case_windows_reserved_device_names` in `tests/test_real_world_edge_cases.py` (`[PASS]`).

---

## BUG-008: NTFS Alternate Data Stream (ADS) Colon Injection Bypass in Output Path Validation

- **Defect ID**: `BUG-008`
- **Severity**: P1 (High / Windows Filesystem Security Vulnerability)
- **Component**: `src/athena/security.py` (`validate_safe_output_path`)
- **Discovered In**: NTFS filesystem boundary penetration audit
- **Resolution Status**: **RESOLVED**

### 1. Symptom & Impact
An attacker or malicious payload could specify an output path containing NTFS Alternate Data Stream syntax (e.g. `notes.md:hidden`), potentially writing hidden data streams or bypassing file auditing.

### 2. Root Cause Analysis
`candidate.resolve()` on Windows handles `:` as a stream separator without raising `ValueError` in some path configurations.

### 3. Engineering Fix
In `validate_safe_output_path`, inspected the raw path string after any drive letter prefix (`C:`). Any occurrence of `:` in the remaining path raises `AthenaValidationError("NTFS_ADS_BLOCKED", ...)`.

### 4. Verification Evidence
- Verified via `test_edge_case_ntfs_alternate_data_streams` in `tests/test_real_world_edge_cases.py` (`[PASS]`).

---

## BUG-009: Python 3.8+ Positional-Only Parameters Missing `/` Marker in Signature Representation

- **Defect ID**: `BUG-009`
- **Severity**: P2 (Medium / AST Formatting & Contract Accuracy)
- **Component**: `src/athena/ast_analyzer.py` (`_format_py_signature`)
- **Discovered In**: Python 3.8+ language feature edge-case test
- **Resolution Status**: **RESOLVED**

### 1. Symptom & Impact
When analyzing functions with positional-only parameters (`def func(x, y, /)`), the extracted signature was formatted as `def func(x, y)` without the `/` marker. This produced inaccurate evidence records and prevented developers from seeing positional-only boundaries.

### 2. Root Cause Analysis
`_format_py_signature` iterated over parameters and emitted `*` for keyword-only parameters, but omitted logic to close positional-only parameter lists with `/`.

### 3. Engineering Fix
Updated `_format_py_signature` to track positional-only parameters and insert `/` immediately following the final `POSITIONAL_ONLY` parameter.

### 4. Verification Evidence
- Verified via `test_edge_case_positional_only_parameters` in `tests/test_real_world_edge_cases.py` (`[PASS]`).

---

## BUG-010: Class `@staticmethod` Unconditionally Strips First Argument When Named `self` or `cls`

- **Defect ID**: `BUG-010`
- **Severity**: P2 (Medium / AST Parameter Arity Accuracy)
- **Component**: `src/athena/ast_analyzer.py` (`_parse_python_symbols`)
- **Discovered In**: Object-oriented design pattern edge-case testing
- **Resolution Status**: **RESOLVED**

### 1. Symptom & Impact
If a class method decorated with `@staticmethod` had its first argument named `self` or `cls` (e.g. `@staticmethod def sanitize(self: str, cls: int)`), `_extract_py_params` stripped `self`, reporting parameter list as `['cls']`. Downstream callers passing 2 arguments were incorrectly flagged as arity mismatches.

### 2. Root Cause Analysis
`_parse_python_symbols` unconditionally passed `is_method=True` for all functions inside class bodies, without checking decorators for `@staticmethod`.

### 3. Engineering Fix
Inspected `m_decs` for `"staticmethod"`. If present, passed `is_method=False`, ensuring the first argument is never stripped.

### 4. Verification Evidence
- Verified via `test_edge_case_staticmethod_and_classmethod_preservation` in `tests/test_real_world_edge_cases.py` (`[PASS]`).

---

## BUG-011: `@property` Getter Signature & Return Annotation Overwritten by `@name.setter`

- **Defect ID**: `BUG-011`
- **Severity**: P2 (Medium / AST Symbol Index Defect)
- **Component**: `src/athena/ast_analyzer.py` (`_parse_python_symbols`)
- **Discovered In**: Python property getter/setter coexistence testing
- **Resolution Status**: **RESOLVED**

### 1. Symptom & Impact
In classes defining both `@property def val(self) -> int:` and `@val.setter def val(self, new_val: int) -> None:`, the setter method overwrote the getter in `symbols[qname]`. The property's return type was reported as `None`, causing spurious `return_type_changed` warnings and breaking doc drift checks.

### 2. Root Cause Analysis
Both functions shared the identical AST name `val` inside the class body, so the dictionary assignment `symbols[f"{class}.{item.name}"] = ...` clobbered the getter with the setter.

### 3. Engineering Fix
If `qname in symbols` and the method is decorated with `.setter` or `.deleter`, Athena retains the getter as the primary public symbol and stores the setter under `f"{qname}.setter"`.

### 4. Verification Evidence
- Verified via `test_edge_case_property_getter_and_setter_coexistence` in `tests/test_real_world_edge_cases.py` (`[PASS]`).

---

## BUG-012: TypeScript Generic Functions (`<T>`), Generic Arrow Functions (`<R>`), and Exported Enums Ignored

- **Defect ID**: `BUG-012`
- **Severity**: P2 (Medium / TypeScript Parser Gap)
- **Component**: `src/athena/ast_analyzer.py` (`_parse_ts_js_symbols`)
- **Discovered In**: TypeScript / JavaScript modern syntax edge-case testing
- **Resolution Status**: **RESOLVED**

### 1. Symptom & Impact
TypeScript exported functions with type parameters (`export function queryItems<T>(collection: string, filter: T)`), generic arrow functions (`export const transform = <R>(data: any): R =>`), and exported enums (`export enum UserRole`) were completely ignored by `_parse_ts_js_symbols`. Changes to their signatures went undetected.

### 2. Root Cause Analysis
1. `_TS_EXPORT_FUNC_RE` and `_TS_EXPORT_ARROW_RE` expected `(` immediately after the function name, failing on `<T>`.
2. `_TS_EXPORT_TYPE_RE` only matched `interface|type`, omitting `enum`.

### 3. Engineering Fix
1. Updated `_TS_EXPORT_FUNC_RE` and `_TS_EXPORT_ARROW_RE` to match optional generic parameter clauses `(?:<[^>]*>)?`.
2. Updated `_TS_EXPORT_TYPE_RE` to match `(?:interface|type|enum)` and extract the specific declaration kind.

### 4. Verification Evidence
- Verified via `test_edge_case_ts_generics_and_enums` in `tests/test_real_world_edge_cases.py` (`[PASS]`).

---

## BUG-013: Aliased Module Imports Blindspot in Caller Blast-Radius Scanner

- **Defect ID**: `BUG-013`
- **Severity**: P1 (High / Semantic Accuracy Defect)
- **Component**: `src/athena/ast_analyzer.py` (`_scan_callers_and_tests_and_docs`)
- **Discovered In**: Cross-file caller blast radius stress testing
- **Resolution Status**: **RESOLVED**

### 1. Symptom & Impact
When a client module imported a mutated symbol under an alias (e.g. `from calc import compute as calculate_val; calculate_val(10)`), Athena's caller scanner failed to identify the call site, falsely reporting zero downstream callers impacted.

### 2. Root Cause Analysis
The AST walker only checked `callee_name in breaking_short_names`. Because `"calculate_val"` was not in `breaking_short_names` (which only contained the original name `"compute"`), the call site was skipped.

### 3. Engineering Fix
If `callee_name` is not in `breaking_short_names`, Athena consults `file_imports`. If `callee_name in file_imports`, it resolves `imported_target = file_imports[callee_name].split(".")[-1]`. If `imported_target in breaking_short_names`, Athena maps the call site to the original symbol and classifies it with `resolution_confidence="exact_ast"`.

### 4. Verification Evidence
- Verified via `test_edge_case_caller_with_aliased_import` in `tests/test_real_world_edge_cases.py` (`[PASS]`).

---

## BUG-014: `.gitconfig` Diff Prefix Spoofing & Commit Log Signature Corruption (`GitNexus#2915` defect)

- **Defect ID**: `BUG-014`
- **Severity**: P1 (High / Git Subprocess Robustness & Parser Reliability)
- **Component**: `src/athena/git_inspector.py` (`run_git_command`, `_parse_commits_log`)
- **Discovered In**: Adversarial `.gitconfig` configuration stress testing (`diff.noprefix=true`, `diff.mnemonicPrefix=true`, `color.ui=always`, `log.showSignature=true`)
- **Resolution Status**: **RESOLVED**

### 1. Symptom & Impact
When a developer's global `~/.gitconfig` or repository `.git/config` set `diff.noprefix = true` or `diff.mnemonicPrefix = true` (which replaces `a/` and `b/` with `i/`, `w/`, `c/`, `o/` or strips prefixes entirely), `_DIFF_FILE_HEADER_RE` (`^diff --git a/(?P<old>.+?) b/(?P<new>.+)$`) failed to match any file headers in `git diff`, resulting in 0 parsed diff hunks. Similarly, `log.showSignature = true` injected multi-line GPG verification blocks ahead of commit hashes in `git log`, corrupting commit boundary parsing.

### 2. Root Cause Analysis
`run_git_command` invoked `git` without overriding user-level diff formatting and color flags, and `_parse_commits_log` invoked `git log` without `--no-show-signature`.

### 3. Engineering Fix
1. Updated `run_git_command` in `src/athena/git_inspector.py` to prepend `-c core.quotepath=false -c diff.noprefix=false -c diff.mnemonicPrefix=false -c color.ui=never` to every Git subprocess invocation.
2. Added `--no-show-signature` to the `git log` invocation in `_parse_commits_log`.

### 4. Verification Evidence
- Verified via `test_bug_014_gitconfig_diff_noprefix_and_mnemonic_immunity` in `tests/test_real_world_edge_cases.py` (`[PASS]`).

---

## BUG-015: Historical Revision Range Caller Lookup Blindspot When Unchanged Files Are Deleted in Working Tree

- **Defect ID**: `BUG-015`
- **Severity**: P1 (High / Historical Git Graph Accuracy)
- **Component**: `src/athena/ast_analyzer.py` (`_scan_callers_and_tests_and_docs`)
- **Discovered In**: Historical tag-to-tag (`v1.0` -> `v2.0`) blast-radius auditing
- **Resolution Status**: **RESOLVED**

### 1. Symptom & Impact
When auditing a historical revision range (e.g., `v1.0` -> `v2.0`) on a repository currently checked out at `v3.0`, unchanged files in `v2.0` that were later removed or refactored in `v3.0` were read from the local working tree (`full_path = repo_path / rel_path`) instead of the `v2.0` Git tree object. If the file no longer existed in `v3.0`, its callers were silently missed.

### 2. Root Cause Analysis
`_scan_callers_and_tests_and_docs` only checked `target_sources_cache` (which cached files modified in the diff range) and otherwise fell back directly to `full_path.read_text()` on disk, ignoring `extraction.resolved_target_sha`.

### 3. Engineering Fix
Updated `_scan_callers_and_tests_and_docs` so that whenever `extraction.resolved_target_sha != "WORKING_TREE"`, unchanged repository files are retrieved via `read_blob_at_revision(repo_path, extraction.resolved_target_sha, rel_path)` first, falling back to the working tree only when `target_ref` is `WORKING_TREE`. Also added AST call-site arity verification (`_check_call_arity_mismatch`) so broken callers at `target_sha` are explicitly flagged with `arity_mismatch_detected=True`.

### 4. Verification Evidence
- Verified via `test_bug_015_historical_revision_blob_caller_lookup_and_arity` in `tests/test_real_world_edge_cases.py` (`[PASS]`).

---

## BUG-016: Markdown Documentation Drift False Negative When `.md` File Is Edited Without Updating Stale Call Arity

- **Defect ID**: `BUG-016`
- **Severity**: P2 (Medium / Documentation Drift Detection Accuracy)
- **Component**: `src/athena/ast_analyzer.py` (`_scan_callers_and_tests_and_docs`, `_count_call_args_in_text`)
- **Discovered In**: Real-world PR doc-drift testing
- **Resolution Status**: **RESOLVED**

### 1. Symptom & Impact
Previously, if a Markdown file (`README.md`) was touched anywhere in the commit range (for example, updating a version badge or heading), `is_doc_unchanged` became `False` and Athena skipped checking `signature_changed` symbols in that document—even when a code snippet inside `README.md` still invoked the function with its old, incompatible argument count (e.g. `compute_score(10, 20)` instead of 3 required arguments).

### 2. Root Cause Analysis
The doc-drift scanner treated any edit to a `.md` file as proof that all symbol references inside that `.md` file had been updated, without inspecting the call-site argument count inside the Markdown snippet.

### 3. Engineering Fix
Implemented `_count_call_args_in_text(line_str, sym_name)` in `src/athena/ast_analyzer.py` and computed `symbol_min_required_args` from the updated Python/TypeScript AST signature. Even when a Markdown file is modified in the commit range, Athena now parses call expressions `sym_name(...)` in the document and flags `DocDriftItem` whenever the example passes fewer arguments than the updated signature requires.

### 4. Verification Evidence
- Verified via `test_bug_016_markdown_doc_drift_when_doc_file_edited_with_stale_arity` in `tests/test_real_world_edge_cases.py` (`[PASS]`).

---

## BUG-017: `(str, Enum)` <-> `StrEnum` Breaking Base Migration (`griffe#501`), Enum Member Removal, and Public Constant Mutation Blindspot

- **Defect ID**: `BUG-017`
- **Severity**: P2 (Medium / Python AST Semantic Coverage)
- **Component**: `src/athena/ast_analyzer.py` (`_parse_python_symbols`, `_compare_python_symbol`)
- **Discovered In**: Competitor failure-mode audit (`griffe#501`) and Python library contract testing
- **Resolution Status**: **RESOLVED**

### 1. Symptom & Impact
1. Migrating a Python class between `class Color(str, Enum)` and `class Color(StrEnum)` alters `str()` and `format()` behavior in Python 3.11+ (`griffe#501`), breaking downstream string serialization, but was only treated as a generic class change without specific semantic explanation or Enum member tracking.
2. Removing an `Enum` member (e.g. `Color.BLUE`) or changing a module-level `UPPER_CASE` public constant (`MAX_RETRIES = 3` -> `5`) was not tracked by `_parse_python_symbols`.

### 2. Root Cause Analysis
`_parse_python_symbols` only extracted `FunctionDef`, `AsyncFunctionDef`, and `ClassDef` headers, ignoring `ast.Assign` / `ast.AnnAssign` inside `Enum` classes and module-level `UPPER_CASE` constants.

### 3. Engineering Fix
1. Extended `_PySymbolInfo` with `enum_members` and `constant_value_repr`.
2. Extracted `Enum` members and module-level `UPPER_CASE` public constants in `_parse_python_symbols`.
3. Added explicit detection in `_compare_python_symbol` for `(str, Enum)` <-> `StrEnum` migration (`griffe#501`), removed/modified `Enum` members, and modified public constants.

### 4. Verification Evidence
- Verified via `test_bug_017_strenum_migration_enum_members_and_public_constants` in `tests/test_real_world_edge_cases.py` (`[PASS]`).

---

## BUG-018: Body-Only Function Mutations Omitted from `untested_modified_symbols`

- **Defect ID**: `BUG-018`
- **Severity**: P2 (Medium / Test Coverage Gap Detection)
- **Component**: `src/athena/ast_analyzer.py` (`_compute_body_ast_dump`, `run_structural_code_analysis`)
- **Discovered In**: Maintainer audit mode verification
- **Resolution Status**: **RESOLVED**

### 1. Symptom & Impact
When a developer altered the internal business logic of a public function (e.g. changing `return amount` to `return amount * 100`) without altering its signature, decorators, or raised exceptions, the function was not added to `modified_symbol_names`. Consequently, `untested_modified_symbols` failed to warn maintainers that a modified function lacked unit test references.

### 2. Root Cause Analysis
`modified_symbol_names` was populated solely from `symbol_changes`, which only recorded signature, decorator, base-class, or exception deltas.

### 3. Engineering Fix
Implemented `_compute_body_ast_dump(node)` in `src/athena/ast_analyzer.py` (normalizing the AST body while stripping docstrings) and stored `body_ast_hash` on `_PySymbolInfo`. In `run_structural_code_analysis`, if `old_sym.body_ast_hash != new_sym.body_ast_hash`, the symbol is added to `modified_symbol_names` even when its external signature is unchanged.

### 4. Verification Evidence
- Verified via `test_bug_018_body_only_function_mutation_flagged_in_untested_symbols` in `tests/test_real_world_edge_cases.py` (`[PASS]`).

---

## BUG-019: Pre-Filter Slicing and Empty `0`-Byte `__init__.py` Files Artificially Degrade `static_parse_coverage_pct`

- **Defect ID**: `BUG-019`
- **Severity**: P1 (High / Coverage & Confidence Metric Accuracy on Large Multi-Language Repos)
- **Component**: `src/athena/ast_analyzer.py` (`_scan_callers_and_tests_and_docs`)
- **Discovered In**: Live multi-commit evaluation loop on `NVIDIA/cuda-python`, `encode/httpx`, and `pallets/click`
- **Resolution Status**: **RESOLVED**

### 1. Symptom & Impact
During live evaluation of the new `AnalysisCoverageReport` across `NVIDIA/cuda-python` (436 changed files, mixed Cython `.pyx`/`.pxd`/CUDA/Python), `encode/httpx` (13,500+ stars), and `pallets/click` (15,600+ stars):
1. `encode/httpx` and `pallets/click` reported `static_parse_coverage_pct` of `95.0%` and `94.5%` instead of `100.0%`, even though 100% of Python files in both repositories had valid syntax.
2. `NVIDIA/cuda-python` reported `static_parse_coverage_pct` of `15.2%` because most `.py` files were skipped before even being inspected.

### 2. Root Cause Analysis
Two compounding logic errors in `_scan_callers_and_tests_and_docs`:
1. **Unfiltered File Budget Slicing**: The scanner sliced `all_files[:250]` *before* filtering for `.py`/`.ts`/`.js`/`.md` extensions, and then counted all `.py` files in `all_files[250:]` as unparsed (`files_skipped_or_err += extra_code`). In Cython/C++/CUDA repositories where the first 250 paths in `git ls-tree` are `.pxd`, `.pyx`, or `.cu` files, Python modules beyond index 250 were never scanned.
2. **Truthiness Check on Empty `__init__.py` Files**: Line 1121 checked `if not content:` instead of `if content is None:`. Valid `0`-byte `__init__.py` package marker files (`content == ""`) evaluated as falsy and were counted as `files_skipped_or_err += 1` rather than being parsed by `ast.parse("")`.

### 3. Engineering Fix
1. Filtered `all_files` into `eligible_scan_files` (matching `.py`, `.ts`, `.tsx`, `.js`, `.jsx`, `.md`, `.rst`) *prior* to applying the scan cap (`max_scan_files = 1000`).
2. Changed `if not content:` to `if content is None:` so `0`-byte `__init__.py` files proceed to `ast.parse("")` and increment `files_parsed_ok`.

### 4. Verification Evidence
- Verified on live multi-commit evaluation of `encode/httpx`, `pallets/click`, and `NVIDIA/cuda-python`, and via `test_bug_019_empty_init_py_and_non_code_files_do_not_degrade_coverage` in `tests/test_real_world_edge_cases.py` (`[PASS]`).

---

## BUG-020: Missing `src/athena/__main__.py` Prevents Direct Package Execution via `python -m athena`

- **Defect ID**: `BUG-020`
- **Severity**: P1 (High / CLI & MCP Package Entrypoint Defect)
- **Component**: `src/athena/__main__.py`
- **Discovered In**: End-to-end CLI & GitHub Action (`python -m athena`) release verification
- **Resolution Status**: **RESOLVED**

### 1. Symptom & Impact
Running `python -m athena <subcommand>` (or configuring an MCP client with `"command": "python", "args": ["-m", "athena", "mcp"]`) failed with `No module named athena.__main__; 'athena' is a package and cannot be directly executed`.

### 2. Root Cause Analysis
While `src/athena/cli.py` implemented `main()` and `pyproject.toml` registered the `athena = "athena.cli:main"` console script, the package directory `src/athena/` lacked a `__main__.py` module required by Python's `-m` package runner.

### 3. Engineering Fix
Created `src/athena/__main__.py` delegating directly to `sys.exit(main())` from `athena.cli`.

### 4. Verification Evidence
- Verified via live execution of `python -m athena release-notes . --base c3c7965 --target HEAD --version v1.0.0 --audience developers --markdown-only` and `test_bug_020_python_m_athena_module_execution` in `tests/test_real_world_edge_cases.py` (`[PASS]`).



