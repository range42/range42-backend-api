"""Range42 overlay operator library (compose / expand_replication / resolve_secrets).

Shared parity harness with the TypeScript side in range42-deployer-ui/src/overlay/
via schema/test-vectors/. Divergence is a red build per spec section 14.
"""
from app.overlay.compose import compose
from app.overlay.expand_replication import ExpandResult, expand_replication
from app.overlay.errors import NotImplementedOperator
from app.overlay.resolve_secrets import resolve_secrets

__all__ = [
    "compose",
    "expand_replication",
    "ExpandResult",
    "NotImplementedOperator",
    "resolve_secrets",
]
