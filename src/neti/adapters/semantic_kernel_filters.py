"""Semantic Kernel, gated through its function-invocation filter.

    from neti.adapters.semantic_kernel_filters import neti_filter

    kernel.add_filter(FilterTypes.FUNCTION_INVOCATION, neti_filter(pf))

Nothing is wrapped here, and that is the point: Semantic Kernel ships a filter pipeline built for
exactly this, so the gate is a registration rather than a change to anybody's plugins. Every kernel
function invocation passes through it, including the ones the model auto-invokes, so a plugin added
later is gated without anybody remembering to wrap it.

**A filter blocks by not calling `next`.** The pipeline is `async def filter(context, next)`, and
awaiting `next(context)` is what runs the function. Returning without awaiting it is how a call is
stopped — there is no boolean to return and no exception to raise, and both of the obvious-looking
alternatives are wrong here: raising escapes the pipeline and ends the invocation instead of letting
the model re-plan, and `FUNCTION_INVOCATION` has no `terminate` flag (that belongs to
`AUTO_FUNCTION_INVOCATION`, which sees only the auto-invoked subset).

**The denial goes in `context.result`.** Semantic Kernel hands that back where the function's own
return value would have gone, so the model reads the sentence with the number in it and narrows its
target, exactly as on every other seam in this package.

**`FUNCTION_INVOCATION`, not `AUTO_FUNCTION_INVOCATION`.** The second one fires only for functions
the model chose automatically, which sounds like precisely the set worth gating and is a trap: a
plugin invoked by another plugin, or by application code that took its argument from a model, would
walk straight past it. Cardinality is a property of the call, not of who proposed it.
"""

from __future__ import annotations

from typing import Any

from neti.preflight import Preflight

__all__ = ["neti_filter"]


def neti_filter(preflight: Preflight, *, functions: list[str] | None = None) -> Any:
    """A function-invocation filter bound to a `Preflight`.

    `functions=` narrows it to named functions; left out, every kernel function invocation is sized.
    An ungated *parameter* is still out of scope rather than denied, which is where coverage is
    declared in this product (SCOPE.md NC-09).
    """

    async def _filter(context: Any, next: Any) -> None:
        from semantic_kernel.functions import FunctionResult

        from neti.adapters.claude_code import normalise_tool
        from neti.core.types import unreadable_arguments

        # `function` is a required field on the context, so it is read directly rather than with a
        # default: a filter that quietly tolerated its absence would be gating nothing and looking
        # installed, which is the worst failure available to a gate.
        function = context.function
        name = str(getattr(function, "name", "") or "")
        if functions is not None and name not in functions:
            await next(context)
            return

        # `KernelArguments` is a mapping, and it carries execution settings alongside the model's
        # arguments. Only the arguments are the call's target, and only they are sent to a resolver.
        arguments = dict(getattr(context, "arguments", None) or {})
        verdict = preflight.check(normalise_tool(name), unreadable_arguments(arguments))

        if verdict.proceeds:
            await next(context)
            return

        # Not awaiting `next` is the block. Setting `result` is what the model reads.
        #
        # `FunctionResult.function` is the function's *metadata*, not the function — passing the
        # `KernelFunction` itself raises a validation error inside the pipeline, which Semantic
        # Kernel then reports as "error occurred while invoking function". A gate that turns a
        # clean refusal into an invocation error is worse than no gate: the agent sees a broken
        # tool rather than a number it can act on.
        context.result = FunctionResult(function=function.metadata, value=verdict.message)

    return _filter
