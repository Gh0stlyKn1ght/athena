"""Create a reproducible multi-commit Git repository in examples/demo_repo for demonstrating Athena."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(repo),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def build_demo_repo(target_dir: Path) -> Path:
    if target_dir.exists():
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    _git(target_dir, "init")
    _git(target_dir, "config", "user.name", "Athena Demo")
    _git(target_dir, "config", "user.email", "demo@athena.local")

    (target_dir / "src").mkdir()
    (target_dir / "tests").mkdir()

    # Commit 1 (v0.1.0)
    (target_dir / "src" / "billing.py").write_text(
        'from enum import Enum\n\n'
        'class InvoiceStatus(str, Enum):\n'
        '    PENDING = "PENDING"\n'
        '    PAID = "PAID"\n\n'
        'def process_payment(user_id: str, amount_cents: int, currency: str = "USD") -> bool:\n'
        '    """Process a customer payment."""\n'
        '    return amount_cents > 0\n\n'
        'def cancel_legacy_invoice(invoice_id: str) -> str:\n'
        '    return f"cancelled:{invoice_id}"\n\n'
        'def fetch_receipt(receipt_id: str, retries: int = 3) -> str:\n'
        '    return f"receipt:{receipt_id}:{retries}"\n',
        encoding="utf-8",
    )
    (target_dir / "src" / "orders.py").write_text(
        'from src.billing import cancel_legacy_invoice, process_payment\n\n'
        'def checkout_order(user_id: str, cents: int) -> bool:\n'
        '    paid = process_payment(user_id, cents, "USD")\n'
        '    if not paid:\n'
        '        cancel_legacy_invoice("inv_001")\n'
        '    return paid\n',
        encoding="utf-8",
    )
    (target_dir / "README.md").write_text(
        '# Billing Service Demo\n\n'
        '```python\n'
        'process_payment("usr_1", 2500, "USD")\n'
        'cancel_legacy_invoice("inv_001")\n'
        '```\n',
        encoding="utf-8",
    )
    (target_dir / "tests" / "test_billing.py").write_text(
        'from src.billing import fetch_receipt\n\n'
        'def test_fetch_receipt():\n'
        '    assert "receipt:" in fetch_receipt("r_1")\n',
        encoding="utf-8",
    )
    _git(target_dir, "add", ".")
    _git(target_dir, "commit", "-m", "feat: initial billing and order processing baseline")
    _git(target_dir, "tag", "v0.1.0")

    # Commit 2: Misleading commit subject ("fix: minor cleanup in billing") that actually:
    # - Adds required parameter `idempotency_key: str` to `process_payment`, changes return type to `dict`, and raises `ValueError`
    # - Removes `cancel_legacy_invoice`
    # - Updates `fetch_receipt(receipt_id: str, retries: int | None = None)` with `if retries is None:` guard (non-breaking!)
    # - Adds new public feature `issue_refund(payment_id: str, reason: str = "customer_request") -> dict`
    (target_dir / "src" / "billing.py").write_text(
        'from enum import Enum\n\n'
        'class InvoiceStatus(Enum):\n'
        '    PENDING = "PENDING"\n'
        '    PAID = "PAID"\n\n'
        'def process_payment(user_id: str, amount_cents: int, idempotency_key: str, currency: str = "USD") -> dict:\n'
        '    """Process a customer payment with mandatory idempotency_key."""\n'
        '    if amount_cents <= 0:\n'
        '        raise ValueError("amount_cents must be positive")\n'
        '    return {"ok": True, "user_id": user_id, "key": idempotency_key, "currency": currency}\n\n'
        'def fetch_receipt(receipt_id: str, retries: int | None = None) -> str:\n'
        '    if retries is None:\n'
        '        retries = 3\n'
        '    return f"receipt:{receipt_id}:{retries}"\n\n'
        'def issue_refund(payment_id: str, reason: str = "customer_request") -> dict:\n'
        '    return {"refunded": payment_id, "reason": reason}\n',
        encoding="utf-8",
    )
    _git(target_dir, "add", ".")
    _git(target_dir, "commit", "-m", "fix: minor cleanup in billing")

    # Commit 3 (v0.2.0): Input validation fix in orders.py + lockfile
    (target_dir / "poetry.lock").write_text('[[package]]\nname = "demo"\nversion = "0.2.0"\n' * 60, encoding="utf-8")
    (target_dir / "src" / "orders.py").write_text(
        'from src.billing import cancel_legacy_invoice, process_payment\n\n'
        'def checkout_order(user_id: str, cents: int) -> bool:\n'
        '    if not user_id:\n'
        '        raise ValueError("user_id is required")\n'
        '    res = process_payment(user_id, cents, "USD")\n'
        '    if not res:\n'
        '        cancel_legacy_invoice("inv_001")\n'
        '    return bool(res)\n',
        encoding="utf-8",
    )
    _git(target_dir, "add", ".")
    _git(target_dir, "commit", "-m", "fix(orders): validate non-empty user_id in checkout_order")
    _git(target_dir, "tag", "v0.2.0")

    return target_dir


if __name__ == "__main__":
    out = build_demo_repo(Path(__file__).resolve().parent / "demo_repo")
    print(f"Created demo repository at: {out}")
