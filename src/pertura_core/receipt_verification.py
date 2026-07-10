from __future__ import annotations

import base64
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from pertura_core.hashing import canonical_json
from pertura_core.models import ResultEnvelope, RunReceipt


def verify_receipt(
    receipt: RunReceipt | dict[str, Any],
    *,
    authoritative_public_key: str,
    expected_result: ResultEnvelope | None = None,
    expected_policy_hash: str | None = None,
) -> bool:
    try:
        parsed = receipt if isinstance(receipt, RunReceipt) else RunReceipt.model_validate(receipt)
        if parsed.public_key != authoritative_public_key:
            return False
        if expected_result is not None:
            if parsed.result_id != expected_result.result_id or parsed.result_hash != expected_result.canonical_hash:
                return False
            if parsed.output_hashes != expected_result.output_hashes:
                return False
        if expected_policy_hash is not None and parsed.policy_hash != expected_policy_hash:
            return False
        raw_public = base64.b64decode(authoritative_public_key.encode("ascii"), validate=True)
        signature = base64.b64decode(parsed.signature.encode("ascii"), validate=True)
        public_key = Ed25519PublicKey.from_public_bytes(raw_public)
        public_key.verify(signature, canonical_json(parsed.signing_payload()).encode("utf-8"))
        return True
    except (ValueError, TypeError, InvalidSignature):
        return False
