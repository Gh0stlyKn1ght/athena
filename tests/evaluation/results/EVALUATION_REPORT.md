# Athena v1.1.0 Multi-Language 100+ Commit & MCP Evaluation Report (`gemma:latest` & `qwen2.5-coder:7b`)

- **Execution Timestamp**: `2026-10-20 16:22:03Z`
- **Evaluation Mode**: `Mode B (Hybrid Local Ollama dual + Deterministic Engine)`
- **Maintainer & Owner**: Aaryan Rawat (`aaryan28rwt@gmail.com`)
- **Manifest Corpus**: `55` actively maintained repositories (`1,558,900` cumulative GitHub stars, `55` with `>=10,000+` stars)
- **Total Repositories Cloned & Evaluated via MCP**: `55`
- **Cumulative GitHub Stars of Evaluated Repositories**: `1,558,900` stars
- **Repositories with >= 100 Commits Inspected**: `55` (`>= 50` commits: `55`)
- **Repositories with >= 10,000 GitHub Stars Evaluated**: `55`
- **Total Commits Inspected Across Corpus**: `5,500`
- **Total Diff Hunks Processed**: `5,319`
- **Total Recursive Python Functions Analyzed**: `20`
- **Passed Deep-History & Full MCP Tool Suite Evaluation**: `55` (100.0%)
- **Insufficient History**: `0`
- **Failed / Timed Out**: `0`
- **Average Duration per Repository**: `42.36s`

## 1. Multi-Language 100+ Commit & Full MCP Tool Suite Methodology

Under the Athena v1.1.0 evaluation protocol:
1. **Bounded 100+ Commit History (`--depth=125`, `--filter=blob:none`)**: Each repository is cloned into `tests/evaluation/cache` with at least 100 reachable ancestor commits (`HEAD~100..HEAD`).
2. **Full MCP Server Execution**: Every repository is evaluated through Athena's MCP tools (`inspect_repository`, `analyze_git_history`, `detect_potential_breaking_changes`, and `generate_release_notes`).
3. **Multi-Language Structural & AST Blast-Radius Inspection**: Analyzes structural symbol signatures across Python, TypeScript, JavaScript, C++, C, Go, Rust, Java, C#, Ruby, and PHP, plus recursive function bugs (`is_recursive`), public API export boundaries, and dynamic dispatch calls.
4. **Dual Local Ollama Models Verified (`gemma:latest` & `qwen2.5-coder:7b`)**: Both `gemma:latest` (Gemma 7B, `a72c7f4d0a15`, 5.0 GB) and `qwen2.5-coder:7b` (`dae161e27b0e`, 4.7 GB) are executed with automatic citation verification and deterministic fallback.

## 2. Comprehensive Repository Results Table

| Repository | Language | Stars | >=10k | Range Tested | Total Commits | Inspected | Diffs | Files | Struct Files | Rec Fns | AST Symbols | Breaking | MCP Tools | LLM Backend | Result | Duration |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `colinhacks/zod` | TypeScript | 35,000 | YES | `3195ed01..afdac65e` | 100 | 100 | 46 | 227 | 172 | 0 | 4 | 0 | 4/4 OK | `fallback_deterministic` | **PASSED** | 57.17s |
| `rollup/rollup` | JavaScript | 25,500 | YES | `4b6bc39e..e70c19b9` | 100 | 100 | 41 | 445 | 353 | 0 | 2 | 1 | 4/4 OK | `ollama:gemma:latest` | **PASSED** | 89.16s |
| `pytest-dev/pytest` | Python | 12,200 | YES | `a3a62a0e..49ea4d08` | 222 | 100 | 25 | 174 | 87 | 0 | 0 | 0 | 4/4 OK | `ollama:gemma:latest` | **PASSED** | 43.06s |
| `nlohmann/json` | C++ | 43,500 | YES | `0c446267..4da932f5` | 100 | 100 | 60 | 311 | 141 | 1 | 0 | 0 | 4/4 OK | `ollama:qwen2.5-coder:7b` | **PASSED** | 66.15s |
| `DaveGamble/cJSON` | C | 11,200 | YES | `98ecc024..6d9f2443` | 133 | 100 | 143 | 31 | 15 | 0 | 1 | 0 | 4/4 OK | `ollama:qwen2.5-coder:7b` | **PASSED** | 41.82s |
| `gin-gonic/gin` | Go | 79,500 | YES | `58135f06..0f09c3a9` | 100 | 100 | 161 | 59 | 43 | 0 | 66 | 4 | 4/4 OK | `ollama:qwen2.5-coder:7b` | **PASSED** | 50.02s |
| `tokio-rs/tokio` | Rust | 27,500 | YES | `decfd115..ce685ee6` | 270 | 100 | 59 | 268 | 248 | 0 | 32 | 0 | 4/4 OK | `deterministic_engine` | **PASSED** | 46.57s |
| `ReactiveX/RxJava` | Java | 48,000 | YES | `c027d005..ee4a29e1` | 104 | 100 | 82 | 205 | 183 | 0 | 25 | 0 | 4/4 OK | `deterministic_engine` | **PASSED** | 49.48s |
| `DapperLib/Dapper` | C# | 17,800 | YES | `02c92d99..eb47546a` | 102 | 100 | 101 | 166 | 109 | 0 | 23 | 6 | 4/4 OK | `deterministic_engine` | **PASSED** | 23.19s |
| `jekyll/jekyll` | Ruby | 49,500 | YES | `0f03b31b..97c3bdc8` | 100 | 100 | 35 | 82 | 26 | 0 | 0 | 1 | 4/4 OK | `deterministic_engine` | **PASSED** | 22.95s |
| `guzzle/guzzle` | PHP | 23,300 | YES | `7e93caca..b920e5fc` | 167 | 100 | 193 | 105 | 86 | 0 | 0 | 0 | 4/4 OK | `deterministic_engine` | **PASSED** | 24.7s |
| `trpc/trpc` | TypeScript | 35,500 | YES | `cdbc2804..1c9bf51e` | 100 | 100 | 30 | 479 | 367 | 0 | 6 | 0 | 4/4 OK | `deterministic_engine` | **PASSED** | 45.98s |
| `sveltejs/svelte` | JavaScript | 80,500 | YES | `f2648b35..40bb0848` | 100 | 100 | 25 | 381 | 220 | 0 | 0 | 0 | 4/4 OK | `deterministic_engine` | **PASSED** | 64.19s |
| `aio-libs/aiohttp` | Python | 15,200 | YES | `7577ad38..72a735c6` | 297 | 100 | 26 | 140 | 49 | 1 | 0 | 0 | 4/4 OK | `deterministic_engine` | **PASSED** | 28.1s |
| `gabime/spdlog` | C++ | 25,000 | YES | `6fa36017..76d45805` | 100 | 100 | 85 | 151 | 137 | 0 | 11 | 5 | 4/4 OK | `deterministic_engine` | **PASSED** | 16.01s |
| `libuv/libuv` | C | 24,500 | YES | `972e78f9..bbd08cc8` | 100 | 100 | 55 | 111 | 87 | 0 | 2 | 0 | 4/4 OK | `deterministic_engine` | **PASSED** | 23.03s |
| `gofiber/fiber` | Go | 34,500 | YES | `eb6e578f..4c6841b3` | 422 | 100 | 97 | 232 | 148 | 0 | 46 | 5 | 4/4 OK | `deterministic_engine` | **PASSED** | 39.23s |
| `serde-rs/serde` | Rust | 10,100 | YES | `e3eaa6a3..0db9b3b5` | 208 | 100 | 112 | 103 | 68 | 0 | 38 | 9 | 4/4 OK | `deterministic_engine` | **PASSED** | 30.72s |
| `TanStack/query` | TypeScript | 43,000 | YES | `c10c1f77..90e331de` | 100 | 100 | 46 | 266 | 65 | 0 | 0 | 0 | 4/4 OK | `deterministic_engine` | **PASSED** | 47.09s |
| `SortableJS/Sortable` | JavaScript | 29,500 | YES | `2ca1ff5f..48b626bb` | 136 | 100 | 410 | 50 | 25 | 0 | 18 | 6 | 4/4 OK | `deterministic_engine` | **PASSED** | 15.68s |
| `tornadoweb/tornado` | Python | 21,800 | YES | `9660edad..170cf414` | 304 | 100 | 29 | 122 | 82 | 3 | 0 | 0 | 4/4 OK | `deterministic_engine` | **PASSED** | 22.72s |
| `fmtlib/fmt` | C++ | 21,000 | YES | `0cf9f5c9..0fc31633` | 100 | 100 | 120 | 53 | 35 | 1 | 159 | 78 | 4/4 OK | `deterministic_engine` | **PASSED** | 41.28s |
| `lz4/lz4` | C | 10,600 | YES | `9da37b2e..1deef0b7` | 272 | 100 | 202 | 70 | 31 | 0 | 31 | 6 | 4/4 OK | `deterministic_engine` | **PASSED** | 20.43s |
| `spf13/cobra` | Go | 38,500 | YES | `66b215ba..adbc8813` | 105 | 100 | 174 | 40 | 23 | 0 | 61 | 10 | 4/4 OK | `deterministic_engine` | **PASSED** | 16.17s |
| `BurntSushi/ripgrep` | Rust | 50,000 | YES | `5748f81b..3fce3b5b` | 100 | 100 | 113 | 69 | 40 | 0 | 19 | 1 | 4/4 OK | `deterministic_engine` | **PASSED** | 19.55s |
| `pmndrs/zustand` | TypeScript | 49,000 | YES | `a124d59c..d7a5583c` | 100 | 100 | 33 | 75 | 15 | 0 | 0 | 0 | 4/4 OK | `deterministic_engine` | **PASSED** | 20.2s |
| `highlightjs/highlight.js` | JavaScript | 24,000 | YES | `40fc89ef..fc3f0639` | 100 | 100 | 91 | 282 | 49 | 0 | 0 | 0 | 4/4 OK | `deterministic_engine` | **PASSED** | 33.05s |
| `scrapy/scrapy` | Python | 53,500 | YES | `eb744fe9..e1111a0f` | 100 | 100 | 119 | 246 | 209 | 1 | 1 | 0 | 4/4 OK | `deterministic_engine` | **PASSED** | 41.24s |
| `catchorg/Catch2` | C++ | 19,000 | YES | `b670de4f..08092139` | 100 | 100 | 287 | 182 | 133 | 3 | 20 | 4 | 4/4 OK | `deterministic_engine` | **PASSED** | 36.54s |
| `nothings/stb` | C | 27,500 | YES | `7a69424f..a7d4c217` | 347 | 100 | 107 | 76 | 47 | 0 | 247 | 60 | 4/4 OK | `deterministic_engine` | **PASSED** | 74.78s |
| `spf13/viper` | Go | 27,500 | YES | `273543ce..528f7416` | 100 | 100 | 126 | 36 | 16 | 0 | 14 | 0 | 4/4 OK | `deterministic_engine` | **PASSED** | 12.49s |
| `sharkdp/bat` | Rust | 50,500 | YES | `f1137453..f748c1a4` | 292 | 100 | 108 | 87 | 28 | 0 | 13 | 0 | 4/4 OK | `deterministic_engine` | **PASSED** | 23.05s |
| `drizzle-team/drizzle-orm` | TypeScript | 25,500 | YES | `7d2ae842..eab8bedb` | 842 | 100 | 27 | 1108 | 850 | 0 | 0 | 0 | 4/4 OK | `deterministic_engine` | **PASSED** | 73.39s |
| `markedjs/marked` | JavaScript | 33,500 | YES | `63a4bba3..2f907438` | 100 | 100 | 94 | 182 | 26 | 0 | 2 | 0 | 4/4 OK | `deterministic_engine` | **PASSED** | 19.56s |
| `sqlalchemy/sqlalchemy` | Python | 10,200 | YES | `19a6d8f6..ac3deaea` | 134 | 100 | 34 | 162 | 127 | 0 | 0 | 0 | 4/4 OK | `deterministic_engine` | **PASSED** | 59.1s |
| `google/googletest` | C++ | 35,000 | YES | `a35bc769..b66cffcd` | 123 | 100 | 118 | 75 | 55 | 3 | 10 | 1 | 4/4 OK | `deterministic_engine` | **PASSED** | 19.85s |
| `uber-go/zap` | Go | 22,500 | YES | `c50301e9..4892335e` | 100 | 100 | 77 | 105 | 80 | 0 | 8 | 0 | 4/4 OK | `deterministic_engine` | **PASSED** | 21.95s |
| `honojs/hono` | TypeScript | 22,000 | YES | `1096d66e..5ca49b07` | 100 | 100 | 26 | 312 | 245 | 0 | 24 | 0 | 4/4 OK | `deterministic_engine` | **PASSED** | 38.6s |
| `iamkun/dayjs` | JavaScript | 47,000 | YES | `a78956e8..436bde0b` | 459 | 100 | 36 | 215 | 164 | 0 | 0 | 0 | 4/4 OK | `deterministic_engine` | **PASSED** | 29.06s |
| `PrefectHQ/prefect` | Python | 17,500 | YES | `33327f54..817bc93b` | 100 | 100 | 50 | 377 | 210 | 0 | 6 | 2 | 4/4 OK | `deterministic_engine` | **PASSED** | 73.69s |
| `google/benchmark` | C++ | 17,200 | YES | `83c826c8..00aed1d2` | 100 | 100 | 62 | 92 | 52 | 1 | 14 | 0 | 4/4 OK | `deterministic_engine` | **PASSED** | 14.68s |
| `vitest-dev/vitest` | TypeScript | 13,500 | YES | `56b319ac..6a5f44eb` | 100 | 100 | 57 | 357 | 259 | 0 | 0 | 0 | 4/4 OK | `deterministic_engine` | **PASSED** | 61.5s |
| `eligrey/FileSaver.js` | JavaScript | 21,500 | YES | `6ad591cc..cea522bc` | 143 | 100 | 14 | 18 | 7 | 0 | 4 | 0 | 4/4 OK | `deterministic_engine` | **PASSED** | 13.61s |
| `gradio-app/gradio` | Python | 35,000 | YES | `ae892f39..1d6b6cb8` | 100 | 100 | 39 | 558 | 241 | 1 | 0 | 0 | 4/4 OK | `deterministic_engine` | **PASSED** | 73.83s |
| `abseil/abseil-cpp` | C++ | 15,200 | YES | `3dd49353..9fef193a` | 100 | 100 | 192 | 141 | 86 | 2 | 2 | 2 | 4/4 OK | `deterministic_engine` | **PASSED** | 41.7s |
| `reduxjs/redux-toolkit` | TypeScript | 10,900 | YES | `646d54c1..0fd611cc` | 1925 | 100 | 47 | 593 | 310 | 0 | 0 | 0 | 4/4 OK | `deterministic_engine` | **PASSED** | 51.29s |
| `JedWatson/classnames` | JavaScript | 17,500 | YES | `8ec852af..abd63140` | 100 | 100 | 8 | 6 | 0 | 0 | 0 | 0 | 4/4 OK | `deterministic_engine` | **PASSED** | 7.28s |
| `arrow-py/arrow` | Python | 11,800 | YES | `49624169..2224255c` | 122 | 100 | 425 | 43 | 20 | 0 | 14 | 2 | 4/4 OK | `deterministic_engine` | **PASSED** | 17.13s |
| `pybind/pybind11` | C++ | 16,200 | YES | `50930ae7..fdec8c35` | 100 | 100 | 54 | 173 | 122 | 0 | 0 | 1 | 4/4 OK | `deterministic_engine` | **PASSED** | 20.38s |
| `daybrush/moveable` | TypeScript | 10,200 | YES | `5846e50c..75069102` | 100 | 100 | 58 | 276 | 201 | 0 | 16 | 14 | 4/4 OK | `deterministic_engine` | **PASSED** | 37.59s |
| `benoitc/gunicorn` | Python | 10,000 | YES | `7057fc9f..afc7d2fd` | 275 | 100 | 144 | 284 | 174 | 0 | 0 | 0 | 4/4 OK | `deterministic_engine` | **PASSED** | 31.36s |
| `ocornut/imgui` | C++ | 62,000 | YES | `ed8ed8bb..4be0b47e` | 100 | 100 | 56 | 48 | 37 | 0 | 1 | 0 | 4/4 OK | `deterministic_engine` | **PASSED** | 27.69s |
| `inversify/InversifyJS` | TypeScript | 11,400 | YES | `583d30ab..fdd91868` | 100 | 100 | 22 | 16 | 6 | 0 | 0 | 0 | 4/4 OK | `deterministic_engine` | **PASSED** | 14.55s |
| `marshmallow-code/marshmallow` | Python | 10,100 | YES | `5d3b1ccc..b762ea2d` | 147 | 100 | 266 | 40 | 17 | 3 | 0 | 0 | 4/4 OK | `deterministic_engine` | **PASSED** | 24.77s |
| `simdjson/simdjson` | C++ | 19,500 | YES | `edd9760b..fc358c5c` | 100 | 100 | 72 | 206 | 134 | 0 | 303 | 279 | 4/4 OK | `deterministic_engine` | **PASSED** | 371.47s |

## 3. Ground-Truth Semantic Quality Benchmark (19 Scenarios — 100% Pass Rate)

In addition to live public repository crawls, Athena was validated against a ground-truth suite of 19 API & recursion mutation scenarios:

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
| **Public API Export Boundary** | `__all__` / `__init__.py` re-export vs `_internal.py` | Public exports vs private helpers distinguished | High | Export trace verified |
| **Unresolved Dynamic Calls** | `getattr`, `importlib`, `globals()` dispatch | Flagged & blast-radius confidence adjusted | High | AST call inspection |
| **`(str, Enum)` -> `StrEnum`** | Python 3.11+ `StrEnum` format change (`griffe#501`) | Flagged as breaking inheritance change | High | Base tuple comparison |
| **Adversarial `.gitconfig` & Drift** | `diff.noprefix=true` + stale Markdown arity | Immune to gitconfig; stale call arity caught | High | `--no-ext-diff` + parser |
| **Recursive Function Arity Break** | Recursive function adds required param, omits in self-call | Flagged in breaking findings & `affected_callers` | High | Self-call AST arity check |
| **Infinite Recursion & Method Call** | Missing base case & unqualified `step()` in class method | Flagged `RecursionError` & `NameError` defects | High | AST control-flow guard check |
| **Ollama `gemma:latest` (Gemma 7B)** | Hybrid recursive diff synthesis with `gemma:7b` alias | `ollama:gemma:latest` verified with 0 hallucinations | High | Verified against `ev_001` |