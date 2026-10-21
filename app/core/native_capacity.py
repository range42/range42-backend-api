"""Original native capacity handshake. Physical acceptance grants no execution."""

import json
from typing import Literal

from pydantic import Field, field_validator, model_validator
from sqlalchemy import text

from app.core import capacity_ledger as ledger
from app.core.physical_capacity import StrictObservation, Digest
from app.core.physical_nodes import NodeAttestation, digest


class NativeReceipt(StrictObservation):
    protocol: Literal["range42-native-capacity-receipt-v1"]
    status: Literal["accepted", "absent"]
    request_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    request_digest: Digest
    node: NodeAttestation
    receipt_revision: int = Field(ge=0, le=1)
    execution_ready: Literal[False]
    infrastructure_qualified: Literal[False]

    @field_validator("execution_ready", "infrastructure_qualified", mode="before")
    @classmethod
    def unqualified(cls, value):
        if value is not False:
            raise ValueError("Capacity acceptance cannot qualify execution")
        return value

    @model_validator(mode="after")
    def revision_matches_status(self):
        if self.receipt_revision != (1 if self.status == "accepted" else 0):
            raise ValueError("Native receipt phase differs")
        return self


def request_for(row, installation_id):
    if not row.native_request_id or not row.native_request_actor:
        raise ledger.refused(
            "CAPACITY_NATIVE_REQUEST_REQUIRED",
            "Journal the original native request before dispatch.",
        )
    # The wire request is the original held demand. Later generation/volume
    # evidence and retirement are separate lifecycle facts, never a replacement
    # for the request the physical node already accepted. Keep all resource and
    # binding fields intact so a changed demand still fails its receipt digest.
    original = ledger.commitment(row).model_dump()
    original['phase'] = 'held'
    for guest in original['guests']:
        guest['materialization'] = None
    return {
        "protocol": "range42-native-capacity-request-v1",
        "request_id": row.native_request_id,
        "installation_id": installation_id,
        "root_policy_revision": row.root_policy_revision,
        "plan_revision": row.plan_revision,
        "binding_revision": row.binding_revision,
        "approval_revision": row.approval_revision,
        "approved_by": row.approved_by,
        "requested_by": row.native_request_actor,
        "commitment": original,
    }


def verified_receipt(row, request, value):
    try:
        receipt = NativeReceipt.model_validate(value)
        node = receipt.node
        if (
            request_for(row, request["installation_id"]) != request
            or receipt.status != "accepted"
            or receipt.receipt_revision != 1
            or receipt.request_id != row.native_request_id
            or receipt.request_digest != digest(request)
            or node.installation_id != request["installation_id"]
            or node.physical_node_id != row.physical_node_id
            or node.hardware_binding != ledger.commitment(row).hardware_binding
            or node.configuration_revision != row.root_policy_revision
            or node.supported_operations
            != ["inspect", "capacity", "capacity-admit", "capacity-status"]
        ):
            raise ValueError()
        return receipt
    except (ValueError, TypeError, KeyError):
        raise ledger.refused(
            "CAPACITY_NATIVE_RECEIPT_MISMATCH",
            "The native reply does not match the original retained request and physical node. Preserve the commitment and reconcile it.",
        ) from None


def root_accepted(row):
    if row.native_receipt_json is None:
        return False
    try:
        value = json.loads(row.native_receipt_json)
        request = request_for(row, value["node"]["installation_id"])
        verified_receipt(row, request, value)
        return True
    except (ValueError, TypeError, KeyError):
        raise ledger.refused(
            "CAPACITY_NATIVE_RECEIPT_MISMATCH",
            "The retained native receipt changed. Preserve the original commitment.",
        ) from None


async def record_acceptance(factory, reviewed, request, value):
    # SSH and HTTPS already completed; never hold this writer over native RPC.
    async with factory() as session:
        await session.execute(text('PRAGMA synchronous=FULL'))
        await session.execute(text("BEGIN IMMEDIATE"))
        row, _, _, _, _, _ = await ledger.current_lifecycle_hold(session, reviewed)
        receipt = verified_receipt(row, request, value)
        original = reviewed.observed.node
        for key in [
            "physical_node_id",
            "hardware_binding",
            "node_name",
            "installation_id",
            "configuration_revision",
            "api_certificate_sha256",
            "node_certificate_sha256",
        ]:
            if getattr(original, key) != getattr(receipt.node, key):
                raise ledger.refused(
                    "CAPACITY_NATIVE_RECEIPT_MISMATCH",
                    "Native acceptance belongs to a different independently verified node.",
                )
        serialized = receipt.model_dump_json()
        if row.native_receipt_json is not None:
            if row.native_receipt_json != serialized:
                raise ledger.refused()
            return row
        row.native_receipt_json = serialized
        row.revision += 1
        row.record_digest = ledger.record_digest(row)
        await session.commit()
        return row
