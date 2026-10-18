# Security Policy

## Supported Versions

The following versions of **Athena (`athena-mcp`)** are currently supported with security updates:

| Version | Supported          |
| ------- | ------------------ |
| `1.0.x` | :white_check_mark: |
| `< 1.0` | :x:                |

---

## Built-In Security Guarantees

Athena is designed from the ground up as a **read-only, local-first** MCP server and CLI:

- **Path Jail & Symlink Escape Defense (`src/athena/security.py`)**: All repository paths and file inspections are canonicalized and confined to the target Git workspace.
- **Git CLI Flag Injection Defense**: Revision references (`base_ref`, `target_ref`) beginning with `-` or containing unsafe control characters are rejected before invoking `git` subprocesses (`shell=False`).
- **Secret & Token Redaction**: Diff excerpts and commit metadata are automatically scrubbed for API keys, Bearer tokens, private keys, and credentials prior to MCP serialization or optional local Ollama prompts.
- **Zero External Network Calls**: Athena never transmits code or metadata to external cloud APIs.

---

## Reporting a Vulnerability

If you discover a security vulnerability in Athena (for example: path traversal, Git argument injection, symlink escape, or credential redaction bypass):

1. **Please do NOT open a public GitHub issue** for exploitable security vulnerabilities.
2. Report the vulnerability privately via **[GitHub Private Vulnerability Reporting](https://github.com/aaryanrwt/athena/security/advisories/new)** on this repository.
3. Include:
   - Affected file(s) and commit SHA (`git rev-parse HEAD`).
   - A minimal proof-of-concept reproduction.
   - Potential impact and suggested remediation (if any).

We will acknowledge receipt within **48 hours** and work with you to validate, patch, and credit your finding in the security advisory.
