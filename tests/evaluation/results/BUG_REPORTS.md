# Athena Formal Defect & Bug Resolution Reports (BUG-001 through BUG-039)

## Overview

During the implementation, adversarial edge-case stress-testing, multi-language recursive function AST analysis, local Ollama (`gemma:latest` / Gemma 7B and `qwen2.5-coder:7b`) integration, and 100-commit MCP evaluation of Athena across 55+ real-world repositories (10,000+ star ecosystems across TypeScript, JavaScript, Python, C++, C, Go, Rust, Java, C#, Ruby, and PHP totaling 3,000k+ cumulative stars), thirty-nine critical engineering bugs and edge-case defects were identified, isolated, reproduced via automated tests, and permanently resolved in source code.

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

---

## BUG-021: Recursive Function Self-Call Blindspot in Caller Blast-Radius Scanner and Arity Checker

- **Defect ID**: `BUG-021`
- **Severity**: P1 (High / Recursive Function Semantic Blindspot)
- **Component**: `src/athena/ast_analyzer.py` (`_scan_callers_and_tests_and_docs`, `_analyze_py_recursion_bugs`)
- **Discovered In**: Recursive function edge-case audit with `gemma:latest` (Gemma 7B)
- **Resolution Status**: **RESOLVED**

### 1. Symptom & Impact
When a recursive function or method (e.g., `def walk_nodes(node: dict, depth: int, max_depth: int)` or `Class.traverse`) added a new required parameter or narrowed its signature, but a recursive call site inside its own body (`return walk_nodes(node.get('child'), depth + 1)`) was not updated with the new argument, Athena completely ignored the recursive call site in `affected_callers` and failed to flag `arity_mismatch_detected=True`. At runtime, the first recursive step crashes with `TypeError: walk_nodes() missing 1 required positional argument: 'max_depth'`.

### 2. Root Cause Analysis
In `_scan_callers_and_tests_and_docs`, lines 1169–1173 contained a line-range skip filter:
```python
if (
    rel_path == sc_obj.file_path
    and sc_obj.target_line_span
    and sc_obj.target_line_span.start_line <= lineno <= sc_obj.target_line_span.end_line
):
    continue
```
This filter was originally intended to avoid treating a function's `def` header line as a call site. However, in Python's `ast` module, `ast.FunctionDef` is never an `ast.Call` node—any `ast.Call` inside `start_line..end_line` with the function's own name is a **recursive self-call**! Skipping the entire `start_line..end_line` span blinded Athena to all recursive self-invocations.

### 3. Engineering Fix
1. Removed the `start_line <= lineno <= end_line` skip filter in `_scan_callers_and_tests_and_docs` so recursive self-calls inside a function's body are captured in `affected_callers` with `enclosing_symbol == symbol_name` and evaluated by `_check_call_arity_mismatch`.
2. Implemented `_analyze_py_recursion_bugs` in `src/athena/ast_analyzer.py` to statically inspect every function/method body (excluding nested `def`/`class`/`lambda` scopes) for self-recursive calls (`func(...)`, `self.method(...)`, `cls.method(...)`, `ClassName.method(...)`) and verify their argument arity against the function's own parameter specification.

### 4. Verification Evidence
- Verified via `test_bug_021_recursive_function_signature_and_self_call_arity_mismatch` in `tests/test_real_world_edge_cases.py` (`[PASS]`) and `SCENARIO-17` in `tests/evaluation/run_quality_benchmark.py` (`[PASS]`).

---

## BUG-022: Unconditional Infinite Recursion (Missing Base Case) and Unqualified Method Self-Call (`NameError`) Blindspot

- **Defect ID**: `BUG-022`
- **Severity**: P1 (High / Recursive Function Defect Detection)
- **Component**: `src/athena/ast_analyzer.py` (`_analyze_py_recursion_bugs`, `_compare_python_symbol`, `_build_added_symbol_recursion_issues`)
- **Discovered In**: Recursive bug pattern synthesis and testing with `gemma:latest` (Gemma 7B)
- **Resolution Status**: **RESOLVED**

### 1. Symptom & Impact
Two high-severity recursive bug classes identified during `gemma:latest` adversarial generation were previously undetected:
1. **Unconditional Infinite Recursion (Missing Base Case)**: A recursive function (`def countdown(n): return countdown(n - 1)`) that invokes itself outside of any conditional guard (`If`, `IfExp`, `While`, `For`, `Match`, `Try`, or short-circuit `BoolOp`) or after an early-exit termination branch (`if n <= 0: return 0`), guaranteeing `RecursionError: maximum recursion depth exceeded` at runtime.
2. **Unqualified Method Self-Recursion (`NameError`)**: Inside a class method `Class.visit(self, node_id)`, calling `visit(node_id - 1)` instead of `self.visit(node_id - 1)` raises `NameError: name 'visit' is not defined` at runtime because Python class scopes are not enclosing lexical scopes for methods.
3. Furthermore, newly added functions (`qname not in old_syms`) containing these recursive bugs were only emitted with `change_kind="added", is_breaking_candidate=False`.

### 2. Root Cause Analysis
Athena previously compared function signatures, decorators, return annotations, and raised exceptions, but did not perform control-flow guard analysis on recursive self-calls or check method receiver qualification (`self.` / `cls.`).

### 3. Engineering Fix
1. Added `_analyze_py_recursion_bugs(node, cls_name, params)` in `src/athena/ast_analyzer.py`, populating `is_recursive`, `has_unconditional_recursion`, `recursive_arity_mismatches`, and `unqualified_method_self_calls` on `_PySymbolInfo`.
2. Wired these checks into both `_compare_python_symbol` (for modified functions) and `_build_added_symbol_recursion_issues` (for newly added functions in `run_structural_code_analysis` and `analyze_python_module_diff`).

### 4. Verification Evidence
- Verified via `test_bug_022_unconditional_infinite_recursion_and_unqualified_method_recursion` in `tests/test_real_world_edge_cases.py` (`[PASS]`) and `SCENARIO-18` in `tests/evaluation/run_quality_benchmark.py` (`[PASS]`).

---

## BUG-023: Unhandled `RecursionError` When Parsing or Unparsing Deeply Nested Python ASTs

- **Defect ID**: `BUG-023`
- **Severity**: P1 (High / Parser Crash Resilience on Deep ASTs)
- **Component**: `src/athena/ast_analyzer.py` (`_unparse_safe`, `_compute_body_ast_dump`, `_parse_python_symbols`, `_scan_callers_and_tests_and_docs`)
- **Discovered In**: Deep AST recursion stress-testing across 10,000+ star repositories
- **Resolution Status**: **RESOLVED**

### 1. Symptom & Impact
In repositories containing auto-generated parsers, deeply chained binary expressions (`1 + 1 + 1 + ...` 3,000+ levels deep), or deeply nested AST structures, Python's `ast.parse`, `ast.unparse`, `ast.dump`, or recursive AST visitor functions raised `RecursionError: maximum recursion depth exceeded`, crashing the analysis pass because `except SyntaxError:` did not catch `RecursionError`.

### 2. Root Cause Analysis
All AST parsing/unparsing `try ... except` blocks in `src/athena/ast_analyzer.py` only caught `SyntaxError` (or `Exception` in a few helpers), leaving `RecursionError` and `MemoryError` uncaught during `ast.parse`, `ast.dump`, and `_visit`.

### 3. Engineering Fix
Updated `_unparse_safe`, `_compute_body_ast_dump`, `_extract_raised_exceptions`, `_extract_none_guarded_params`, `_extract_py_imports`, `_parse_python_symbols`, `_scan_callers_and_tests_and_docs`, and `_collect_init_reexports` to catch `(SyntaxError, RecursionError, MemoryError)` and degrade gracefully without crashing the analysis pipeline.

### 4. Verification Evidence
- Verified via `test_bug_023_deeply_nested_ast_recursion_error_immunity` in `tests/test_real_world_edge_cases.py` (`[PASS]`).

---

## BUG-024: Same-Short-Name Method Collision in `breaking_short_names` Clobbers Caller Blast Radius Across Classes

- **Defect ID**: `BUG-024`
- **Severity**: P2 (Medium / Multi-Class Caller Attribution Accuracy)
- **Component**: `src/athena/ast_analyzer.py` (`_scan_callers_and_tests_and_docs`)
- **Discovered In**: Multi-class recursive method caller blast-radius testing
- **Resolution Status**: **RESOLVED**

### 1. Symptom & Impact
When two classes in a repository (`OrderHandler.execute` and `PaymentHandler.execute`) both underwent breaking changes in the same commit range, `breaking_short_names["execute"] = sc.symbol_name` overwrote `"OrderHandler.execute"` with `"PaymentHandler.execute"`. Consequently, `OrderHandler.execute` received `0` callers in `caller_impacts`, and calls inside `OrderHandler` were misattributed to `PaymentHandler.execute`.

### 2. Root Cause Analysis
`breaking_short_names` was a `dict[str, str]` mapping a short method name to only a single `symbol_name` string, and `breaking_by_qname[sc.symbol_name] = sc` overwrote higher-severity changes if a symbol had multiple `SymbolSignatureChange` entries.

### 3. Engineering Fix
Replaced single-string lookup in `_scan_callers_and_tests_and_docs` with `breaking_qnames_by_short: dict[str, list[str]]` and narrowed `self.method(...)` / `cls.method(...)` calls inside an enclosing class `ClassA.method` to the matching `ClassA.*` qualified symbol. Also preserved the highest-severity `SymbolSignatureChange` in `breaking_by_qname`.

### 4. Verification Evidence
- Verified via `test_bug_024_same_short_name_method_collision_across_classes` in `tests/test_real_world_edge_cases.py` (`[PASS]`).

---

## BUG-025: Local Ollama `gemma:latest` (Gemma 7B) Tag Alias Mismatch, CUDA Context OOM, and Top-Level JSON Story Normalization

- **Defect ID**: `BUG-025`
- **Severity**: P1 (High / Local Ollama Model Compatibility — `gemma:latest` / Gemma 7B)
- **Component**: `src/athena/ollama_engine.py`, `src/athena/mcp_server.py`, `src/athena/cli.py`
- **Discovered In**: Live integration and benchmarking of newly downloaded `gemma:latest` (`a72c7f4d0a15`, 5.0 GB)
- **Resolution Status**: **RESOLVED**

### 1. Symptom & Impact
When running Athena with the newly downloaded Gemma 7B model in Ollama (`gemma:latest`, 5.0 GB disk, 7.8 GB active memory footprint with 256k token vocabulary on a 6 GB VRAM RTX 4050 GPU):
1. Requesting `model="gemma:7b"` passed `check_ollama_model_ready` (which checked `m.startswith(model.split(":")[0])`), but `analyze_changes_with_ollama` sent `"model": "gemma:7b"` to `/api/generate`, resulting in HTTP 404 because the installed tag in Ollama is `"gemma:latest"`.
2. Requesting `num_ctx=2048, num_predict=768` with `gemma:latest` caused CUDA memory pressure (HTTP 500 on model swap) or slow generation across the 54% GPU / 46% CPU split.
3. With `"format": "json"`, `gemma:latest` frequently returned a top-level single story object `{"story_id": "...", "title": "...", ...}` or `{"story": {...}}` rather than `{"stories": [...]}`—and sometimes returned string values instead of 1-element lists for `observed_facts` or `evidence_ids`, causing `parsed.get("stories", [])` to yield `[]`.

### 2. Root Cause Analysis
`ollama_engine.py` assumed the caller's `model` string always matched the exact Ollama tag name, hardcoded `num_ctx=2048, num_predict=768` for all models regardless of vocabulary/VRAM footprint, and only read `parsed.get("stories", [])`.

### 3. Engineering Fix
1. Added `resolve_ollama_model_name(base_url, model)` in `src/athena/ollama_engine.py` to resolve prefix aliases (`"gemma:7b"` / `"gemma"` -> `"gemma:latest"`, `"qwen2.5-coder"` -> `"qwen2.5-coder:7b"`).
2. Tuned `gemma*` inference options to `num_ctx=1024, num_predict=160` with an automatic 1-step retry on HTTP 500 (`num_ctx=1024, num_predict=128`).
3. Normalized top-level single-story JSON dicts (`{"story_id": ...}`, `{"story": {...}}`, or JSON arrays) and coerced string fields (`observed_facts`, `affected_files`, `evidence_ids`) into lists before running `verify_and_hydrate_stories`.
4. Exposed `ollama_model` across all MCP tools in `src/athena/mcp_server.py` and `--model` in `src/athena/cli.py`.

### 4. Verification Evidence
- Verified live against `http://127.0.0.1:11434` with `gemma:latest` (`a72c7f4d0a15`) via `test_bug_025_ollama_gemma_7b_alias_resolution_and_json_normalization` in `tests/test_real_world_edge_cases.py` (`[PASS]`) and `SCENARIO-19` in `tests/evaluation/run_quality_benchmark.py` (`[PASS]`).

---

## BUG-026: Missing Error Boundaries and Python `bool`-as-`int` Subclass Bypass in MCP Tool Entrypoints

- **Defect ID**: `BUG-026`
- **Severity**: P2 (Medium / MCP Protocol Error Reporting & Input Validation)
- **Component**: `src/athena/mcp_server.py`, `pyproject.toml`
- **Discovered In**: MCP tool adversarial input fuzzing
- **Resolution Status**: **RESOLVED**

### 1. Symptom & Impact
1. In Python, `bool` is a subclass of `int` (`isinstance(True, int) == True` and `True == 1`). Passing `max_recent_commits=True` to `inspect_repository` or `max_commits=True` to `analyze_git_history` bypassed `if not isinstance(max_commits, int) or max_commits < 1` and silently truncated commit inspection to 1 commit instead of returning `"INVALID_MAX_COMMITS"`.
2. Calling `analyze_git_history(repo_path="")`, `generate_release_notes(repo_path="")`, or `detect_potential_breaking_changes(repo_path="")` lacked the explicit `EMPTY_REPO_PATH` guard present in `inspect_repository` and lacked top-level `try ... except Exception` error boundaries.
3. `pyproject.toml` declared `athena-mcp = "athena.mcp_server:main"`, whereas `mcp_server.py` named the function `run_mcp_stdio_server`.

### 2. Engineering Fix
1. Added `isinstance(max_commits, bool)` rejection and explicit `EMPTY_REPO_PATH` checks to all MCP tool functions in `src/athena/mcp_server.py`.
2. Wrapped all MCP tool implementations in structured `try ... except Exception` blocks returning schema-compliant error dictionaries (`status="error"`, `error_code`, `error_message`).
3. Added `main = run_mcp_stdio_server` in `src/athena/mcp_server.py`.

### 3. Verification Evidence
- Verified via `test_bug_026_mcp_server_error_boundaries_on_adversarial_inputs` in `tests/test_real_world_edge_cases.py` (`[PASS]`).

---

## BUG-027: `pytest` Collection Recurses into Cloned Repositories Cached Under `tests/evaluation/cache`

- **Defect ID**: `BUG-027`
- **Severity**: P2 (Medium / Test Harness Isolation Defect)
- **Component**: `pyproject.toml` (`[tool.pytest.ini_options]`)
- **Discovered In**: Full test suite execution after populating `tests/evaluation/cache` with multi-repository clones
- **Resolution Status**: **RESOLVED**

### 1. Symptom & Impact
Because `pyproject.toml` configured `testpaths = ["tests"]` without `norecursedirs`, once `tests/evaluation/cache/` contained cloned repositories (`goldmansachs/gs-quant`, `pallets/click`, `pallets/flask`, `psf/requests`), running `python -m pytest` descended into `tests/evaluation/cache/*/tests` and attempted to import third-party test suites, failing with 143 `ModuleNotFoundError` collection errors.

### 2. Engineering Fix
Added `norecursedirs = ["cache", ".git", ".venv", "__pycache__", "build", "dist"]` under `[tool.pytest.ini_options]` in `pyproject.toml`.

### 3. Verification Evidence
- Verified by running `python -m pytest -v`: collected exactly 48 Athena test items and passed **48/48 (100%)** with zero collection errors.

---

## BUG-028: `git log --numstat` and `git diff -M` Trigger Lazy Network Blob Fetches and `TimeoutExpired` on Partial Clones (`--filter=blob:none`)

- **Defect ID**: `BUG-028`
- **Severity**: P1 (High / Partial Clone & Large Monorepo Scalability Defect)
- **Component**: `src/athena/git_inspector.py` (`_is_promisor_partial_clone`, `_parse_commits_log`, `extract_revision_range_data`)
- **Discovered In**: Large-scale MCP evaluation across 10,000+ star partial clones (`ollama/ollama`, `microsoft/playwright`, `vitejs/vite`, `nestjs/nest`, `google/protobuf`)
- **Resolution Status**: **RESOLVED**

### 1. Symptom & Impact
When repositories are cloned with `git clone --filter=blob:none --depth=75` (standard Git partial clone for large monorepos), local `.git/objects` contains commit and tree objects plus the `HEAD` working-tree blobs, while historical ancestor blobs remain on the promisor remote (`remote.origin.promisor=true`).
Running `inspect_repository` or `extract_revision_range_data` executed `git log --numstat` across 50 commits and `git diff -M` across the revision range. Both `--numstat` and `-M` require computing byte-level content diffs and similarity scores across every historical intermediate blob, forcing Git to spawn synchronous `git fetch` requests to `github.com` during a local `git log` / `git diff` command and exceeding the 15-second subprocess timeout (`TimeoutExpired`).

### 2. Root Cause Analysis
`_parse_commits_log` unconditionally passed `--numstat` to `git log`, and `extract_revision_range_data` unconditionally passed `-M` without path filtering to `git diff`, even when `.git/config` had `remote.origin.promisor=true`.

### 3. Engineering Fix
1. Added `_is_promisor_partial_clone(repo_path)` in `src/athena/git_inspector.py` to detect `remote.origin.promisor` / `extensions.partialclone` in `.git/config`.
2. Updated `_parse_commits_log` to use `--name-only` instead of `--numstat` on promisor partial clones so `git log` resolves strictly from local tree objects in `<50ms` with zero network traffic.
3. Updated `extract_revision_range_data` on partial clones to omit `-M`, prioritize AST/doc source files (`.py`, `.ts`, `.tsx`, `.js`, `.jsx`, `.md`, `.rst`), and bound the unified diff pathspec to 20 non-lockfile paths so all required historical blobs are fetched in a single batch packfile request.

### 4. Verification Evidence
- Verified across 55+ cloned `10,000+` star partial clones in `tests/evaluation/cache` (`ollama/ollama`, `microsoft/playwright`, `vitejs/vite`, `nestjs/nest`, `google/protobuf`, `redis/redis`, `scikit-learn/scikit-learn`, `pandas-dev/pandas`, etc.): per-repository evaluation time dropped from `>60s` timeout to `~1.8s–4.5s`.

---

## BUG-029: Redundant `git cat-file` / `git show` Subprocess Storms for `HEAD` Blobs and Unbatched Partial-Clone Paths

- **Defect ID**: `BUG-029`
- **Severity**: P2 (Medium / Subprocess & I/O Performance Defect)
- **Component**: `src/athena/git_inspector.py` (`extract_revision_range_data`), `src/athena/ast_analyzer.py` (`_scan_callers_and_tests_and_docs`)
- **Discovered In**: Profiling `analyze_git_history`, `detect_potential_breaking_changes`, and `generate_release_notes` across 50-commit ranges
- **Resolution Status**: **RESOLVED**

### 1. Symptom & Impact
1. In `extract_revision_range_data`, when `target_ref="HEAD"`, Athena spawned two subprocesses (`git cat-file -s` and `git show`) for every modified target file even though the file was already checked out in the local working tree (`repo_path / file_path`).
2. Furthermore, on partial clones (`is_partial=True`), calling `read_blob_at_revision(repo_path, base_sha, ...)` on files outside the batched `git diff` pathspec triggered individual sequential HTTPS blob fetches (`1.5s` each).
3. In `_scan_callers_and_tests_and_docs` (`src/athena/ast_analyzer.py`), scanning candidate caller/test/doc files spawned `read_blob_at_revision` subprocesses even when `resolved_target_sha` matched the checked-out `HEAD` working tree.

### 2. Engineering Fix
1. In `extract_revision_range_data` (`src/athena/git_inspector.py`), when `target_ref` is `"HEAD"` or `"WORKING_TREE"`, target blobs are read directly from the validated working tree on disk (`wt_candidate.read_text`), and on partial clones `base_sha` blob reads are restricted to `batched_partial_paths` (already cached locally by `git diff`).
2. In `_scan_callers_and_tests_and_docs` (`src/athena/ast_analyzer.py`), candidate caller/test/doc files existing on disk in the working tree are read directly without spawning `git show` subprocesses.

### 3. Verification Evidence
- Verified via `pytest` (`100%` pass rate) and full 4-tool MCP evaluation across 55+ repositories in `tests/evaluation/cache`.

---

## BUG-030: Optional Positional Parameter Insertion Shifting Surviving Positional Indices Not Flagged as Breaking ($M$-Binding Relation Defect)

- **Defect ID**: `BUG-030`
- **Severity**: P1 (High / Python AST Positional Binding Defect)
- **Component**: `src/athena/ast_analyzer.py` (`_compare_python_symbol`)
- **Discovered In**: External audit by Massimiliano Brighindi (`M` binding-relation model)
- **Resolution Status**: **RESOLVED**

### 1. Symptom & Impact
When an optional positional parameter was inserted *before* an existing positional parameter (e.g., `def f(a, b=2):` $\to$ `def f(a, inserted=0, b=2):`), `_compare_python_symbol` checked `added_req`, `removed_params`, `reordered`, and `narrowed_kinds`, all of which evaluated to false because `[p for p in old_pos_names if p in new_pos_names]` preserved relative order `['a', 'b']`. Callers invoking `f(1, 2)` positional-wise silently bound `2` to `inserted` instead of `b`, yet Athena classified the change as non-breaking (`severity="low"`).

### 2. Engineering Fix
Added positional index preservation checks in `_compare_python_symbol` (`old_pos_idx[p] != new_pos_idx[p]` for every surviving positional parameter `p` when `not old.has_varargs`), flagging `shifted_pos` as `is_breaking_candidate=True` (`severity="high"`) while keeping trailing optional positional appends and keyword-only (`*`) insertions non-breaking.

### 3. Verification Evidence
- Verified across the 66-case parametrized `test_m_binding_matrix` and `test_bug_030_optional_positional_parameter_insertion_shifts_surviving_indices` in `tests/test_real_world_edge_cases.py` (`[PASS]`).

---

## BUG-031: Alphabetical Doc, Changelog, and Test File Starvation of Production Source Blobs on Partial Clones

- **Defect ID**: `BUG-031`
- **Severity**: P1 (High / Multi-Language AST Coverage Starvation Defect)
- **Component**: `src/athena/git_inspector.py` (`extract_revision_range_data`, `_path_priority_key`)
- **Discovered In**: Deep audit of 55-repository 100-commit v1.1 evaluation (`pytest-dev/pytest`, `nlohmann/json`, `sveltejs/svelte`, `guzzle/guzzle`, `jekyll/jekyll`, `drizzle-team/drizzle-orm`, `vitest-dev/vitest`, `gradio-app/gradio`)
- **Resolution Status**: **RESOLVED**

### 1. Symptom & Impact
Across 20 of the 55 evaluated repositories over 100-commit windows, `structural.symbol_changes` reported `0` AST symbol changes even though hundreds of files were modified in the commit range.

### 2. Root Cause Analysis
`git diff --name-status` returns changed paths in alphabetical order (`.` $\to$ `A-Z` $\to$ `a-z`). On partial clones (`--filter=blob:none`), `extract_revision_range_data` iterated `prelim_paths` in alphabetical order and filled the `max_ast_blob_files = 15` budget with `.changeset/*.md`, `CHANGELOG.md`, `README.md`, `benchmark/`, `changelog/`, `docs/`, `examples/`, and `Test*` files before ever reaching `include/`, `lib/`, `packages/`, or `src/`. Furthermore, `selected_ast_code_paths` included `.md` and `.rst` files (`SUPPORTED_DOC_EXTS`), allowing documentation files to consume all 15 AST blob slots.

### 3. Engineering Fix
1. Implemented a 4-tier priority key `_path_priority_key(fpath, status)` in `src/athena/git_inspector.py`:
   - Tier 0: Production code files (`SUPPORTED_AST_CODE_EXTS`, excluding tests and docs), prioritizing `modified`/`deleted` files (`sub_rank=0`) over `added` files (`sub_rank=1`) so both `base` and `target` blobs are compared.
   - Tier 1: Documentation files (`.md`, `.rst`), capped at 4 files on partial clones.
   - Tier 2: Test files (`_is_test_file`), capped at 4 files on partial clones.
   - Tier 3: Other auxiliary/config files.
2. Selected `selected_ast_code_paths` strictly from Tier 0 production code files (`max_ast_blob_files = 25` on partial clones, `80` on full clones).

### 4. Verification Evidence
- Re-running on cached 100-commit ranges recovered rich AST symbol deltas across all previously starved repositories: `guzzle/guzzle` (`0` $\to$ `233` symbol changes), `nlohmann/json` (`0` $\to$ `220`), `pybind/pybind11` (`0` $\to$ `177`), `jekyll/jekyll` (`0` $\to$ `38`), `pytest-dev/pytest` (`0` $\to$ `30`), `sveltejs/svelte` (`0` $\to$ `16`).
- Verified via `test_bug_031_and_bug_039_priority_ast_blob_selection_over_docs_and_multilang_tests` in `tests/test_real_world_edge_cases.py` (`[PASS]`).

---

## BUG-032: Asymmetric Blob Load & Cross-Header `#include` Relocation False Positives (`fmtlib/fmt`, `simdjson`, `nothings/stb`)

- **Defect ID**: `BUG-032`
- **Severity**: P1 (High / False-Positive Breaking Change Spike)
- **Component**: `src/athena/ast_analyzer.py` (`run_structural_code_analysis`), `src/athena/git_inspector.py` (`IGNORED_DIR_PREFIXES`)
- **Discovered In**: Deep bug-hunting audit of `fmtlib/fmt`, `simdjson/simdjson`, and `nothings/stb`
- **Resolution Status**: **RESOLVED**

### 1. Symptom & Impact
1. In `fmtlib/fmt`, moving 66 functions/types from `include/fmt/base.h` into `include/fmt/core.h` while `include/fmt/base.h` retained `#include "core.h"` caused Athena to flag 66 false-positive breaking `removed` symbols.
2. In `simdjson/simdjson` and `nothings/stb`, deleting bundled third-party code under `dependencies/jsoncppdist/jsoncpp.cpp` or legacy headers under `deprecated/stb.h` caused hundreds of false-positive breaking removals.
3. If a `modified` file had `base_src` loaded but `target_src` skipped (e.g. due to blob size limits), `run_structural_code_analysis` treated `target_src = None` as if every symbol in the file had been deleted.

### 2. Engineering Fix
1. Added a guard in `run_structural_code_analysis`: when `fdelta.status == "modified"` and either `base_src is None` or `target_src is None`, skip asymmetric symbol diffing so missing blobs never synthesize fake mass deletions or additions.
2. Built a cross-file target symbol index (`all_target_py_symbols` and `all_target_multilang_symbols`) across `extraction.target_blobs`. When a symbol is removed from `fpath` (`include/fmt/base.h`), if `fpath` contains a forwarding `#include` / `export *` / `from . import` and the symbol exists in another target file in the same revision (`include/fmt/core.h`), classify it as a non-breaking header/module relocation (`is_breaking_candidate=False`, `severity="low"`).
3. Added `"dependencies/"`, `"deps/"`, `"external/"`, and `"deprecated/"` to `IGNORED_DIR_PREFIXES`.

### 3. Verification Evidence
- `fmtlib/fmt` breaking changes dropped from `78` to `12` genuine changes; `nothings/stb` dropped from `60` to `7` genuine changes.
- Verified via `test_bug_032_cross_header_include_relocation_not_flagged_as_breaking` in `tests/test_real_world_edge_cases.py` (`[PASS]`).

---

## BUG-033: $O(\text{files} \times \text{symbols} \times \text{lines})$ Regex Explosion in Caller & Test Scanning (`simdjson/simdjson` 371s Hang)

- **Defect ID**: `BUG-033`
- **Severity**: P1 (High / Algorithmic Performance Bottleneck)
- **Component**: `src/athena/ast_analyzer.py` (`_scan_callers_and_tests_and_docs`)
- **Discovered In**: Runtime profiling of `simdjson/simdjson` (`371.47s`), `nothings/stb` (`74.83s`), and `fmtlib/fmt` (`41.32s`)
- **Resolution Status**: **RESOLVED**

### 1. Symptom & Impact
`simdjson/simdjson` took `371.47s` (~6.2 minutes) to complete a single repository evaluation because `_scan_callers_and_tests_and_docs` executed `re.search(rf"\b{re.escape(short_sym)}\b", content)` inside a loop over every modified symbol (`430+` symbols) for every test file, and `re.search(rf"\b{re.escape(short_sym)}\s*\(", line)` inside a nested loop over every line of `240` candidate C++ files (`50,000+` lines).

### 2. Engineering Fix
Pre-compiled two combined single-pass regular expressions (`_mod_test_re` and `_breaking_call_re`) outside the candidate file loop in `_scan_callers_and_tests_and_docs`, and skipped scanning lines before checking if the file content contains any target symbol at all.

### 3. Verification Evidence
- `simdjson/simdjson` total evaluation latency dropped from **`371.47s` to `7.33s` (50.7x speedup)**.
- `nothings/stb` latency dropped from **`74.83s` to `5.92s` (12.6x speedup)**.
- `fmtlib/fmt` latency dropped from **`41.32s` to `5.65s` (7.3x speedup)**.

---

## BUG-034: Multi-Language Indentation, Class Method, and Ruby `def self.foo` Collisions

- **Defect ID**: `BUG-034`
- **Severity**: P1 (High / Multi-Language Parser Accuracy Defect)
- **Component**: `src/athena/ast_analyzer.py` (`_CPP_FUNC_RE`, `_RB_PHP_SWIFT_FUNC_RE`, `_TS_CLASS_METHOD_RE`)
- **Discovered In**: Deep inspection of `nlohmann/json`, `pybind/pybind11`, `jekyll/jekyll`, and `sveltejs/svelte`
- **Resolution Status**: **RESOLVED**

### 1. Symptom & Impact
1. `_CPP_FUNC_RE` lacked `^\s*` at the start of the line, so C++ functions and methods indented inside `namespace nlohmann { ... }` or `class binary_reader { ... }` were completely invisible (`0` symbols parsed in `nlohmann/json` and `pybind11`).
2. `_RB_PHP_SWIFT_FUNC_RE` matched `def (?P<name>[A-Za-z_]\w*)`. In Ruby (`jekyll/jekyll`), every class method `def self.process(...)`, `def self.reset(...)` matched `name="self"`, colliding into a single symbol `"self"` and generating false signature changes whenever any class method appeared after another.
3. `_parse_ts_js_symbols` only matched top-level `export function` and `export const = () =>`, missing TypeScript/JavaScript class methods and `export declare function`.

### 2. Engineering Fix
1. Added `^\s*` support and namespace/class indentation handling to `_CPP_FUNC_RE`.
2. Updated `_RB_PHP_SWIFT_FUNC_RE` to `(?P<name>(?:self\.)?[A-Za-z_]\w*[?!]?)` so Ruby class methods preserve their full `self.method_name` identifier and punctuation suffixes (`?`, `!`).
3. Added `_TS_CLASS_METHOD_RE` and `export declare function` support to `_parse_ts_js_symbols`.

### 3. Verification Evidence
- Verified via `test_bug_034_and_bug_038_cpp_indented_functions_ruby_self_methods_and_return_statement_immunity` in `tests/test_real_world_edge_cases.py` (`[PASS]`).

---

## BUG-035: Multi-Language Recursive Function & Broken Self-Call Arity Analysis Missing for Non-Python Languages

- **Defect ID**: `BUG-035`
- **Severity**: P1 (High / Multi-Language Recursive AST Analysis Gap)
- **Component**: `src/athena/ast_analyzer.py` (`detect_multilang_recursive_functions`, `count_recursive_functions_in_source`), `tests/evaluation/run_evaluation.py`
- **Discovered In**: Auditing `recursive_functions_analyzed` across the 45 non-Python repositories in the 55-repo evaluation
- **Resolution Status**: **RESOLVED**

### 1. Symptom & Impact
39 out of 45 non-Python repositories (`TypeScript`, `JavaScript`, `C++`, `C`, `Go`, `Rust`, `Java`, `C#`, `Ruby`, `PHP`) reported `recursive_functions_analyzed = 0` because both `ast_analyzer.py` and `run_evaluation.py` only inspected Python `ast.FunctionDef` nodes for recursion.

### 2. Engineering Fix
1. Implemented `_extract_brace_or_end_body`, `_strip_comments_and_strings`, `detect_multilang_recursive_functions`, and `count_recursive_functions_in_source` in `src/athena/ast_analyzer.py` supporting brace-delimited languages (`TS`, `JS`, `C`, `C++`, `Go`, `Rust`, `Java`, `C#`, `PHP`) and `def ... end` blocks (`Ruby`).
2. Integrated `detect_multilang_recursive_functions` into `run_structural_code_analysis` so newly introduced recursive self-call arity mismatches in non-Python functions are flagged with exact line and argument-count evidence.
3. Updated `tests/evaluation/run_evaluation.py` to count verified recursive functions across all `SUPPORTED_ALL_CODE_EXTS` files.

### 3. Verification Evidence
- Verified via `test_bug_035_and_bug_037_multilang_recursive_function_detection_and_delegated_call_immunity` in `tests/test_real_world_edge_cases.py` (`[PASS]`).

---

## BUG-036: `gemma:latest` JSON Output Truncation at `num_predict=128` on Long Monorepo File Paths (`colinhacks/zod`)

- **Defect ID**: `BUG-036`
- **Severity**: P2 (Medium / Local Ollama `gemma:latest` Output Truncation)
- **Component**: `src/athena/ollama_engine.py`
- **Discovered In**: Live dual-model evaluation of `colinhacks/zod` with `gemma:latest`
- **Resolution Status**: **RESOLVED**

### 1. Symptom & Impact
During the 55-repo evaluation, `colinhacks/zod` (`packages/zod/src/v4/classic/tests/datetime.test.ts`, etc.) caused `gemma:latest` to hit the `num_predict=128` token ceiling before closing its JSON object, triggering `fallback_deterministic` (`98.2%` native LLM completion instead of `100%`).

### 2. Engineering Fix
Increased `is_gemma` generation window in `src/athena/ollama_engine.py` to `num_ctx=1536, num_predict=220` (with fallback retry at `num_ctx=1280, num_predict=180`).

### 3. Verification Evidence
- Live `gemma:latest` synthesis on `colinhacks/zod` now completes cleanly with `ollama:gemma:latest` in `~11.8s` (`100%` native dual-model completion).

---

## BUG-037: Delegated Object Method Calls (`$this->uriFactory->createUri($uri)`, `parent::__construct()`) Misclassified as Recursive Self-Calls

- **Defect ID**: `BUG-037`
- **Severity**: P1 (High / Multi-Language Recursion False Positive)
- **Component**: `src/athena/ast_analyzer.py` (`detect_multilang_recursive_functions`)
- **Discovered In**: Deep bug hunting on `guzzle/guzzle` (`src/Psr7/HttpFactory.php`, `src/Handler/CurlFactory.php`)
- **Resolution Status**: **RESOLVED**

### 1. Symptom & Impact
In `guzzle/guzzle`, a method `public function createUri(string $uri = ''): UriInterface` that delegated to `$this->uriFactory->createUri($uri)` or `parent::createUri(...)` matched `\bcreateUri\s*\(` inside its own body. Because `$this->uriFactory->createUri(...)` had a different receiver object (`$this->uriFactory` rather than `$this`), treating it as a recursive self-call produced false recursion counts and false arity mismatches.

### 2. Engineering Fix
Added receiver-prefix inspection in `detect_multilang_recursive_functions` (`src/athena/ast_analyzer.py`): when a call is preceded by `->`, `.`, or `::`, it is only classified as a recursive self-call if the immediate receiver token is `this`, `$this`, `self`, `static`, or the enclosing class name (explicitly rejecting `parent::`, `super.`, and chained member objects like `$this->factory->`).

### 3. Verification Evidence
- Eliminated all 33 false-positive recursive arity warnings in `guzzle/guzzle` (`46` $\to$ `13` genuine breaking changes) and `jekyll/jekyll` (`36` $\to$ `1` genuine breaking change).
- Verified via `test_bug_035_and_bug_037_multilang_recursive_function_detection_and_delegated_call_immunity` in `tests/test_real_world_edge_cases.py` (`[PASS]`).

---

## BUG-038: C++ `return func(arg, "literal");` Statements Matched as Function Signatures When `;` Allowed in `_CPP_FUNC_RE`

- **Defect ID**: `BUG-038`
- **Severity**: P1 (High / C/C++ Parser False Positive)
- **Component**: `src/athena/ast_analyzer.py` (`_CPP_FUNC_RE`, `_parse_cpp_c_symbols`)
- **Discovered In**: Deep bug hunting on `nlohmann/json` (`include/nlohmann/detail/input/binary_reader.hpp`)
- **Resolution Status**: **RESOLVED**

### 1. Symptom & Impact
When `_CPP_FUNC_RE` was relaxed to allow `;` after `)` so header prototypes could be matched alongside `{` definitions, C++ return statements such as `return get_cbor_string(result, "cbor");` inside function bodies matched `ret="return"`, `name="get_cbor_string"`, and overwrote the real function definition's parameter list with the call site's arguments!

### 2. Engineering Fix
1. Restricted `_CPP_FUNC_RE` to require `{` (`(?=\{)`) after `)` and explicit qualifiers.
2. Captured `(?P<ret>...)` and rejected any match where `ret` contains C/C++ statement keywords (`return`, `else`, `throw`, `delete`, `co_return`, `new`, `case`, `goto`) or where `params_raw` contains string literals (`"` or `'`).

### 3. Verification Evidence
- Eliminated all 27 false-positive C++ call-site collisions in `nlohmann/json` (`45` $\to$ `18` genuine breaking signature changes).
- Verified via `test_bug_034_and_bug_038_cpp_indented_functions_ruby_self_methods_and_return_statement_immunity` in `tests/test_real_world_edge_cases.py` (`[PASS]`).

---

## BUG-039: Multi-Language Test File Detection Missing `_test.go`, `*Test.java`, `*Tests.cs`, `*Test.php`, `*_spec.rb`

- **Defect ID**: `BUG-039`
- **Severity**: P2 (Medium / Multi-Language Test Classification Defect)
- **Component**: `src/athena/git_inspector.py` (`_is_test_file`, `_is_doc_file`)
- **Discovered In**: Auditing test vs. production file prioritization across Go, Java, C#, PHP, and Ruby repositories
- **Resolution Status**: **RESOLVED**

### 1. Symptom & Impact
`_is_test_file` originally checked only `/tests/`, `/test/`, `/__tests__/`, `test_*.py`, `*_test.py`, and `.spec.ts`, missing Go `*_test.go`, Rust `*_test.rs`, Java/C#/PHP `*Test.java`, `*Tests.cs`, `*Test.php`, Ruby `*_spec.rb`, and `/testing/` or `/features/` directories. As a result, Go/Java/PHP/Ruby test files were treated as Tier 0 production code files during AST blob selection and caller blast-radius classification.

### 2. Engineering Fix
Expanded `_is_test_file` in `src/athena/git_inspector.py` to recognize `_test.go`, `_test.rs`, `_test.c`, `_test.cpp`, `_spec.rb`, `_test.rb`, `*Test.java`, `*Tests.java`, `*Test.cs`, `*Tests.cs`, `*Test.php`, `*Tests.php`, and `/testing/`, `/spec/`, `/specs/`, `/features/`, `/e2e/` directory segments.

### 3. Verification Evidence
- Verified via `test_bug_031_and_bug_039_priority_ast_blob_selection_over_docs_and_multilang_tests` in `tests/test_real_world_edge_cases.py` (`[PASS]`).


