from __future__ import annotations

import base64

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from pertura_core import CapabilityRunRequest, ResultEnvelope, RunReceipt, verify_receipt
from pertura_core.hashing import canonical_json


class ReceiptSigner:
    def __init__(self, private_key: Ed25519PrivateKey | None = None) -> None:
        self._private_key = private_key or Ed25519PrivateKey.generate()

    @property
    def public_key_b64(self) -> str:
        raw = self._private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        return base64.b64encode(raw).decode("ascii")

    def sign_result(
        self,
        request: CapabilityRunRequest,
        result: ResultEnvelope,
        *,
        policy_hash: str,
        broker_instance_id: str,
    ) -> RunReceipt:
        unsigned = RunReceipt(
            run_id=request.run_id,
            request_id=request.request_id,
            result_id=result.result_id,
            result_hash=result.canonical_hash,
            capability_id=request.capability_id,
            capability_version=request.capability_version,
            contract_id=request.contract_id,
            contract_hash=request.contract_hash,
            scope_hash=request.scope.canonical_hash,
            policy_hash=policy_hash,
            dependency_hashes={item.object_id: item.object_hash for item in request.dependencies},
            output_hashes=result.output_hashes,
            broker_instance_id=broker_instance_id,
            public_key=self.public_key_b64,
        )
        signature = self._private_key.sign(canonical_json(unsigned.signing_payload()).encode("utf-8"))
        payload = unsigned.model_dump(mode="json")
        payload["signature"] = base64.b64encode(signature).decode("ascii")
        payload["canonical_hash"] = ""
        return RunReceipt.model_validate(payload)

    def sign_bytes(self, payload: bytes) -> str:
        return base64.b64encode(self._private_key.sign(payload)).decode("ascii")


def verify_detached_signature(*, public_key_b64: str, signature_b64: str, payload: bytes) -> bool:
    try:
        public_key = Ed25519PublicKey.from_public_bytes(
            base64.b64decode(public_key_b64.encode("ascii"), validate=True)
        )
        signature = base64.b64decode(signature_b64.encode("ascii"), validate=True)
        public_key.verify(signature, payload)
        return True
    except (InvalidSignature, TypeError, ValueError):
        return False
