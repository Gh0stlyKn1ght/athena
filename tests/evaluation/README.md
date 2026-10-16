# Athena Public Repository Evaluation Harness & Benchmark

This directory contains the reproducible evaluation harness and repository corpus used to validate Athena against real-world public repositories from major technology companies, financial institutions, and prominent open-source projects.

---

## 1. Corpus Selection Criteria

The manifest at [`repositories.json`](repositories.json) contains **176 curated public repositories** selected according to the following criteria:

1. **High Ecosystem Adoption**: Over 10,000 verified GitHub stars, or foundational software in critical software supply chains.
2. **Major Technology Companies**: Public repositories maintained by **NVIDIA** (`cuda-python`, `NeMo`, `apex`, `cutlass`, `DALI`), **Microsoft** (`TypeScript`, `vscode`, `playwright`, `pyright`, `terminal`), **Google / DeepMind** (`jax`, `protobuf`, `flatbuffers`, `optax`), **Meta** (`react`, `zstd`, `pytorch`, `jest`), **Apple** (`swift`, `pkl`), and **AWS** (`aws-cli`, `aws-sdk-js-v3`).
3. **Major Financial Institutions**: Public open-source repositories from **Goldman Sachs** (`goldmansachs/gs-quant`), **FINOS** (`finos/perspective`, `finos/legend`), and **Bloomberg** (`bloomberg/blpapi-python`, `bloomberg/bde`).
4. **Premier Open-Source Developer Tools**: Foundational Python tools (`flask`, `requests`, `fastapi`, `pydantic`, `pytest`, `uv`, `ruff`, `httpx`, `sqlalchemy`, `django`, `black`), TypeScript/JavaScript platforms (`express`, `next.js`, `tailwindcss`, `redux`, `lodash`, `axios`, `fastify`, `nest`, `vite`, `deno`, `node`), and systems projects (`git`, `curl`, `redis`, `go`, `rust`, `kubernetes`, `terraform`).

---

## 2. Evaluation Tiers

The evaluation runner ([`run_evaluation.py`](run_evaluation.py)) subjects each repository to multiple evaluation tiers:

1. **Smoke Test**: Validates repository recognition, `.git` metadata discovery, branch and tag resolution, and clean working tree inspection.
2. **Functional Git Test**: Performs bounded revision range extraction (`HEAD~1` to `HEAD` or tag-to-tag), commit log parsing with Conventional Commit classification, and unified diff extraction.
3. **AST & Structural Analysis**:
   - For Python repositories: Evaluates public symbol changes using Python's built-in `ast` module (parameters, return type annotations, decorator modifications, newly raised exceptions, base-class alterations, and `default=None` body-fallback guards).
   - For TypeScript/JavaScript repositories: Extracts exported functions, classes, interfaces, types, and arrow functions via Athena's structural regex scanner.
4. **Evidence Integrity Test**: Verifies that every reported finding and logical story references a real commit SHA, existing file path, and valid line spans from the repository.
5. **Deterministic Release Notes**: Generates complete, audience-tailored Markdown release notes in Mode A (100% offline, zero LLM calls).
6. **Breaking Change Detection**: Flags contract changes requiring human review and attaches caller blast-radius citations.

---

## 3. Running the Evaluation Suite

You can execute the evaluation runner from PowerShell or Bash:

```powershell
# Evaluate the first 15 repositories in the manifest (default)
python tests/evaluation/run_evaluation.py --limit 15

# Evaluate only Python repositories
python tests/evaluation/run_evaluation.py --limit 20 --filter-lang Python

# Run full evaluation with a custom per-repository timeout
python tests/evaluation/run_evaluation.py --limit 100 --timeout 60
```

### Safety and Resource Controls
- **Read-Only**: The harness never pushes, commits, or mutates any external repository.
- **Bounded Shallow Clones**: Employs `--depth 15 --no-tags --single-branch` to minimize bandwidth and storage requirements.
- **Automatic Cleanup**: Cloned repositories are stored in isolated temporary directories and deleted immediately upon evaluation completion.

---

## 4. Evaluation Results

Results are written automatically to:
- [`results/evaluation_results.json`](results/evaluation_results.json): Machine-readable JSON summary and per-repository breakdown.
- [`results/EVALUATION_REPORT.md`](results/EVALUATION_REPORT.md): Human-readable Markdown summary with timing and category statistics.
