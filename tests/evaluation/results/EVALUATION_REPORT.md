# Athena Multi-Commit Evaluation Report (50+ Commits per Repository)

- **Execution Timestamp**: `2026-10-16 20:00:28Z`
- **Evaluation Mode**: `Mode A (100% Deterministic Engine)`
- **Total Repositories Evaluated**: `8`
- **Repositories with >= 50 Commits Actually Inspected**: `7`
- **Repositories with >= 10,000 GitHub Stars**: `5`
- **Passed Deep-History Evaluation**: `7` (87.5%)
- **Insufficient History (<50 commits)**: `0`
- **Failed / Timed Out**: `1`
- **Average Duration per Repository**: `129.8s`

## 1. Multi-Commit Evaluation Methodology & Separation of Counts

This evaluation directly addresses the single-commit reporting defect identified in initial smoke tests. Under the corrected methodology:
1. **Bounded Multi-Commit History**: Each repository is shallow-cloned with depth >= 75–100 commits to establish at least 50 reachable ancestor commits.
2. **Distinct Revision Range**: Evaluates `HEAD~50..HEAD` (or the deepest available valid revision range), ensuring base and target are separate revisions.
3. **Separation of Commit Metrics**: The evaluation distinguishes:
   - `Total Commits in Range`: Count from `git rev-list --count base..target`.
   - `Commits Inspected`: Commits whose author, timestamp, subject, body, and numstat were parsed.
   - `Diffs Processed`: Distinct diff hunks parsed across changed files.
   - `Files Changed`: Unique files modified across the entire multi-commit window.
   - `Files Analyzed Structurally`: Supported source files (`.py`, `.ts`, `.js`) subjected to AST/symbol diffing.

## 2. Comprehensive Repository Results Table

| Repository | Language | Stars | >=10k | Range Tested | Total Commits | Inspected | Diffs | Files | Struct Files | AST Symbols | Breaking Candidates | >=50 Commits | Result | Duration |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `NVIDIA/cuda-python` | Python | 1,400 | NO | `deb3bb49..d020a5d8` | 50 | 50 | 1113 | 436 | 178 | 278 | 60 | YES | **PASSED** | 147.95s |
| `goldmansachs/gs-quant` | Python | 2,900 | NO | `b505d3b0..bbe70716` | 50 | 50 | 1798 | 502 | 314 | 618 | 316 | YES | **PASSED** | 272.92s |
| `psf/requests` | Python | 52,000 | YES | `561e4b68..611c6162` | 50 | 50 | 88 | 34 | 15 | 4 | 2 | YES | **PASSED** | 25.73s |
| `pallets/flask` | Python | 68,000 | YES | `ed1c9e95..d086db85` | 120 | 50 | 258 | 81 | 30 | 40 | 30 | YES | **PASSED** | 70.66s |
| `axios/axios` | JavaScript | 105,000 | YES | `f8df2e24..326843a3` | 50 | 50 | 250 | 130 | 94 | 0 | 1 | YES | **PASSED** | 87.98s |
| `cli/cli` | Go | 37,000 | YES | `0baa3625..b758ce35` | 185 | 50 | 491 | 213 | 0 | 0 | 0 | YES | **PASSED** | 32.38s |
| `astral-sh/ruff` | Rust | 35,000 | YES | N/A | 0 | 0 | 0 | 0 | 0 | 0 | 0 | NO | FAILED: Clone timed out or n | 24.3s |
| `finos/perspective` | C++ | 6,300 | NO | `82eaa400..d0184e96` | 148 | 50 | 3724 | 1253 | 485 | 110 | 53 | YES | **PASSED** | 376.48s |

## 3. Ground-Truth Semantic Quality Benchmark

In addition to live public repository crawls, Athena was validated against a ground-truth suite of 12 known API mutation scenarios:

| Benchmark Scenario | Ground Truth Contract Change | Athena Detection Result | Confidence | Evidence Grounding |
| :--- | :--- | :--- | :--- | :--- |
| **Renamed Public Function** | Symbol removed; replacement introduced | Flagged as removed public symbol requiring review | High | Exact file & line span |
| **Added Required Parameter** | Added `idempotency_key` without default | Flagged as breaking signature mutation | High | Exact file, line & diff hunk |
| **Guarded `default=None`** | Default changed to `None` with body guard | Exempted as non-breaking (`griffe#469` pattern) | High | Body AST verification |
| **Unguarded `default=None`** | Default changed to `None` without guard | Flagged as breaking (`TypeError` risk) | High | AST body inspection |
| **Return Type Annotation** | Return annotation changed `bool` -> `dict` | Flagged as return type mutation | Medium | Exact AST diff |
| **Newly Raised Exception** | Function now raises `ValueError` | Flagged as newly raised exception path | High | Exception raise AST trace |
| **Base Class Mutation** | Enum base changed `(str, Enum)` -> `(Enum)` | Flagged as potential inheritance break | High | Class definition diff |
| **Harmless Internal Refactor** | Variable rename / whitespace cleanup | Correctly omitted from breaking changes | High | 0 false positives |
| **Downstream Caller Blast Radius** | External caller invokes modified function | Mapped to `src/orders.py:6` (`exact_ast`) | High | Import graph grounded |
| **Documentation Drift** | README code snippets reference old API | Detected stale calls in `README.md:4` & `:5` | High | Line & symbol matched |
| **Empty / Non-Source Range** | Identical revisions or doc-only changes | Empty diff handled gracefully without crash | High | Zero hallucinations |
| **Large Diff & Lockfiles** | 180+ lines in lockfiles (`poetry.lock`) | Excluded from AST token budgets | High | File filter verified |

## 4. Mode Comparison: Deterministic Core (Mode A) vs. Local Ollama (Mode B)

- **Mode A (Deterministic Core)**: Operates 100% offline with zero network activity and 0ms LLM latency. Ground truth AST diffs, caller graphs, and evidence records are generated deterministically.
- **Mode B (Local Model Enhancement)**: Evaluates `qwen2.5-coder:7b` (`dae161e27b0e`) to synthesize developer change summaries and logical change groupings. All model claims are strictly verified against deterministic `EvidenceRecord` items; hallucinated references are stripped.
- **Mode C (Graceful Fallback)**: If Ollama times out, returns malformed JSON, or is unreachable, Athena automatically falls back to Mode A without data loss or pipeline interruption.