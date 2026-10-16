# Athena Ground-Truth Semantic Quality Benchmark Report

## Executive Summary

- **Benchmark Scenarios Evaluated**: 12
- **Mode A (Deterministic AST Engine) Pass Rate**: 12/12 (**100.0%**)
- **Execution Engine**: 100% Deterministic Python AST & Diff Engine (0 network calls, 0 LLM hallucinations)
- **Evaluation Date**: 2026-10-17

## Benchmark Scenario Matrix

| ID | Scenario | Expected | Mode A Result | Latency | Pass/Fail | Evidence / Details |
|---|---|---|---|---|---|---|
| SCENARIO-01 | Function Rename (Symbol Removal) | Breaking | Breaking | 5.75 ms | **PASS** | Detected 1 breaking removed symbol: ['calculate_tax'] |
| SCENARIO-02 | Required Parameter Added | Breaking | Breaking | 0.37 ms | **PASS** | Detected added required parameter breaking change: ["Added required parameter(s) ['timeout'] without default values to 'fetch_data'."] |
| SCENARIO-03 | Parameter Removed | Breaking | Breaking | 0.33 ms | **PASS** | Detected parameter removed breaking change: ["Removed parameter(s) ['timeout'] from 'connect' without *args/**kwargs absorption."] |
| SCENARIO-04 | Optional Parameter Added with Default | Non-Breaking | Non-Breaking | 0.23 ms | **PASS** | Correctly classified as non-breaking optional addition (is_breaking=False). |
| SCENARIO-05 | Unguarded default=None Change | Breaking | Breaking | 0.22 ms | **PASS** | Flagged unguarded default=None change as breaking: ["Default value for parameter 'val' in 'process' changed from 10 to None."] |
| SCENARIO-06 | Guarded default=None Change | Non-Breaking | Non-Breaking | 0.3 ms | **PASS** | Correctly identified guard and marked non-breaking (is_breaking=False). |
| SCENARIO-07 | Return Type Annotation Changed | Breaking | Breaking | 0.14 ms | **PASS** | Flagged return type change as breaking: ["Return type annotation of 'get_user_id' changed from 'int' to 'str'."] |
| SCENARIO-08 | Newly Raised Exception | Breaking | Breaking | 0.24 ms | **PASS** | Flagged new exception as breaking: ["Function 'parse_record' now raises new exception type(s) ['ValueError'] not raised in base revision."] |
| SCENARIO-09 | Base Class Method Signature Mutation | Breaking | Breaking | 0.22 ms | **PASS** | Flagged base class method signature change: ["Added required parameter(s) ['ctx'] without default values to 'BaseHandler.handle'."] |
| SCENARIO-10 | Harmless Internal Refactoring | Non-Breaking | Non-Breaking | 0.24 ms | **PASS** | Zero false positive breaking changes detected (is_breaking=False). |
| SCENARIO-11 | Caller Blast Radius Tracing | Breaking | Breaking | 921.98 ms | **PASS** | Found 2 call sites across tree: ['pkg/caller_a.py', 'pkg/caller_b.py'] |
| SCENARIO-12 | Lockfile Exclusion & Doc Drift Detection | Breaking | Breaking | 1418.09 ms | **PASS** | Lockfiles excluded (0 breaks: True); Doc drift flagged: ["Docstring for 'connect' documents parameter 'timeout' which does not exist in function signature."] |

## Detailed Scenario Analysis

### SCENARIO-01: Function Rename (Symbol Removal)
- **Description**: Renaming a public function is breaking because external callers will raise NameError/AttributeError.
- **Expected Classification**: Breaking Contract Change
- **Mode A Classification**: Breaking Contract Change
- **Evaluation Latency**: 5.75 ms
- **Evidence Records**: 2
- **Observation & Verification**: Detected 1 breaking removed symbol: ['calculate_tax']

### SCENARIO-02: Required Parameter Added
- **Description**: Adding a parameter without a default value breaks existing callers passing positional/keyword args.
- **Expected Classification**: Breaking Contract Change
- **Mode A Classification**: Breaking Contract Change
- **Evaluation Latency**: 0.37 ms
- **Evidence Records**: 1
- **Observation & Verification**: Detected added required parameter breaking change: ["Added required parameter(s) ['timeout'] without default values to 'fetch_data'."]

### SCENARIO-03: Parameter Removed
- **Description**: Removing an optional or required parameter breaks callers explicitly supplying that argument.
- **Expected Classification**: Breaking Contract Change
- **Mode A Classification**: Breaking Contract Change
- **Evaluation Latency**: 0.33 ms
- **Evidence Records**: 1
- **Observation & Verification**: Detected parameter removed breaking change: ["Removed parameter(s) ['timeout'] from 'connect' without *args/**kwargs absorption."]

### SCENARIO-04: Optional Parameter Added with Default
- **Description**: Adding an optional parameter with default is backward-compatible and should NOT trigger a breaking change.
- **Expected Classification**: Backward-Compatible Non-Breaking
- **Mode A Classification**: Backward-Compatible Non-Breaking
- **Evaluation Latency**: 0.23 ms
- **Evidence Records**: 1
- **Observation & Verification**: Correctly classified as non-breaking optional addition (is_breaking=False).

### SCENARIO-05: Unguarded default=None Change
- **Description**: Changing default to None without guard raises runtime TypeError (val + 5 fails if None). Must be flagged.
- **Expected Classification**: Breaking Contract Change
- **Mode A Classification**: Breaking Contract Change
- **Evaluation Latency**: 0.22 ms
- **Evidence Records**: 1
- **Observation & Verification**: Flagged unguarded default=None change as breaking: ["Default value for parameter 'val' in 'process' changed from 10 to None."]

### SCENARIO-06: Guarded default=None Change
- **Description**: Changing default to None WITH body fallback guard (if val is None) is safe; should NOT flag as breaking.
- **Expected Classification**: Backward-Compatible Non-Breaking
- **Mode A Classification**: Backward-Compatible Non-Breaking
- **Evaluation Latency**: 0.3 ms
- **Evidence Records**: 1
- **Observation & Verification**: Correctly identified guard and marked non-breaking (is_breaking=False).

### SCENARIO-07: Return Type Annotation Changed
- **Description**: Changing return type from int to str breaks downstream typed consumers and type checkers.
- **Expected Classification**: Breaking Contract Change
- **Mode A Classification**: Breaking Contract Change
- **Evaluation Latency**: 0.14 ms
- **Evidence Records**: 1
- **Observation & Verification**: Flagged return type change as breaking: ["Return type annotation of 'get_user_id' changed from 'int' to 'str'."]

### SCENARIO-08: Newly Raised Exception
- **Description**: Raising an unhandled ValueError breaks callers that do not catch it (documented griffe#498 limitation).
- **Expected Classification**: Breaking Contract Change
- **Mode A Classification**: Breaking Contract Change
- **Evaluation Latency**: 0.24 ms
- **Evidence Records**: 1
- **Observation & Verification**: Flagged new exception as breaking: ["Function 'parse_record' now raises new exception type(s) ['ValueError'] not raised in base revision."]

### SCENARIO-09: Base Class Method Signature Mutation
- **Description**: Changing method signature in base class breaks overriding subclasses.
- **Expected Classification**: Breaking Contract Change
- **Mode A Classification**: Breaking Contract Change
- **Evaluation Latency**: 0.22 ms
- **Evidence Records**: 1
- **Observation & Verification**: Flagged base class method signature change: ["Added required parameter(s) ['ctx'] without default values to 'BaseHandler.handle'."]

### SCENARIO-10: Harmless Internal Refactoring
- **Description**: Pure body refactor with identical signature and return type must produce ZERO breaking changes.
- **Expected Classification**: Backward-Compatible Non-Breaking
- **Mode A Classification**: Backward-Compatible Non-Breaking
- **Evaluation Latency**: 0.24 ms
- **Evidence Records**: 0
- **Observation & Verification**: Zero false positive breaking changes detected (is_breaking=False).

### SCENARIO-11: Caller Blast Radius Tracing
- **Description**: Traces repo-wide call sites when a function contract changes across import styles.
- **Expected Classification**: Breaking Contract Change
- **Mode A Classification**: Breaking Contract Change
- **Evaluation Latency**: 921.98 ms
- **Evidence Records**: 2
- **Observation & Verification**: Found 2 call sites across tree: ['pkg/caller_a.py', 'pkg/caller_b.py']

### SCENARIO-12: Lockfile Exclusion & Doc Drift Detection
- **Description**: Excludes lockfile updates from API breaks; identifies phantom parameters in docstrings as doc drift.
- **Expected Classification**: Breaking Contract Change
- **Mode A Classification**: Breaking Contract Change
- **Evaluation Latency**: 1418.09 ms
- **Evidence Records**: 1
- **Observation & Verification**: Lockfiles excluded (0 breaks: True); Doc drift flagged: ["Docstring for 'connect' documents parameter 'timeout' which does not exist in function signature."]
