"""Advertising-platform protocol implementations and account authorization."""

from adjutant.adapters.conformance import (
    CapabilitySet,
    ChannelAdapter,
    create_adapter,
    run_adapter_conformance,
    run_all_conformance,
)

__all__ = [
    "CapabilitySet",
    "ChannelAdapter",
    "create_adapter",
    "run_adapter_conformance",
    "run_all_conformance",
]
