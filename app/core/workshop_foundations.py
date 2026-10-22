"""Compatibility imports for previously saved template preparation clients."""
from app.core.template_preparation import (  # noqa: F401
    FoundationDescriptor, FoundationSpec, FoundationAllocationAuthority,
    compile_foundation, foundation_topology, resolve_native_foundation,
    allocation_authority, allocation_check, refused,
)
