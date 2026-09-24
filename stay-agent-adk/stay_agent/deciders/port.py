"""One port, three implementations, chosen by table (I8).

A decider answers a closed question with a value from a closed option list. It never
routes, never writes and never speaks to the guest: it proposes a choice, and the code
that asked decides what that choice is worth.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from ..contracts import Decision


@runtime_checkable
class TypedDecider(Protocol):
    name: str

    async def decide(self, question: str, options: list[str], state: dict[str, Any]) -> Decision:
        ...


class DeciderUnavailable(RuntimeError):
    """The decider did not answer in shape. The caller falls back; it never improvises."""
