"""Token-budget accounting for gateway responses (spec Section 4 token discipline).

The JSONL protocol is described as token-bounded: every response the gateway
returns to the agent should be small enough to fit the per-turn UI-state budget.
This module makes that property *measurable* and *optionally enforceable* without
pulling in a model tokenizer dependency.

:func:`estimate_tokens` serializes an object to compact JSON and applies a
documented ~4-chars-per-token heuristic. It is an estimate, not a model-exact
count: real tokenizers split on sub-word boundaries, whitespace, and punctuation
differently. A concrete tokenizer (e.g. ``tiktoken`` or a llama.cpp vocabulary)
could be injected later by swapping the estimator; the :class:`TokenBudget`
surface (``measure`` / ``within`` / ``annotate``) would stay the same.
"""

from __future__ import annotations

import json
import math
from typing import Any

#: Average characters per token for the heuristic estimator. English/JSON text
#: tokenizes to roughly four characters per token; this is intentionally coarse.
CHARS_PER_TOKEN = 4

#: The field name the gateway adds to annotated response payloads.
ESTIMATED_TOKENS_FIELD = "estimated_tokens"


def estimate_tokens(obj: Any) -> int:
    """Estimate the token count of ``obj`` once serialized to compact JSON.

    The object is serialized with ``json.dumps(separators=(",", ":"),
    ensure_ascii=False)`` (the same compact framing the wire uses) and the
    character length is divided by :data:`CHARS_PER_TOKEN`, rounding up. An empty
    serialization yields zero tokens.

    This is a heuristic, not a model-exact count. It exists to make the
    token-bounded property observable; a real tokenizer can be substituted later.
    """
    text = json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
    if not text:
        return 0
    return math.ceil(len(text) / CHARS_PER_TOKEN)


class TokenBudget:
    """Measure and optionally cap the estimated token size of a payload.

    When ``max_tokens`` is ``None`` the budget is unbounded: :meth:`measure` and
    :meth:`annotate` still work, and :meth:`within` is always ``True``. When a
    ceiling is set, :meth:`within` reports whether an object fits, and callers may
    use it to decide whether to raise an enforcement error.
    """

    def __init__(self, max_tokens: int | None = None) -> None:
        self.max_tokens = max_tokens

    def measure(self, obj: Any) -> int:
        """Return the estimated token count of ``obj`` (see :func:`estimate_tokens`)."""
        return estimate_tokens(obj)

    def within(self, obj: Any) -> bool:
        """Report whether ``obj`` fits the ceiling. Always ``True`` if unbounded."""
        if self.max_tokens is None:
            return True
        return self.measure(obj) <= self.max_tokens

    def annotate(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Return a copy of ``payload`` with an ``estimated_tokens`` field added.

        The returned dict is a shallow copy; the input ``payload`` is not mutated.
        The estimate is computed over the payload *before* the field is added, so
        the count reflects the operation result rather than itself.
        """
        annotated = dict(payload)
        annotated[ESTIMATED_TOKENS_FIELD] = self.measure(payload)
        return annotated
