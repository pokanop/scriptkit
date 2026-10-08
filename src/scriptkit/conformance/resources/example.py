"""Runnable v1 API recipe: python example.py [--json] [--cancel|--fail]."""

import sys

from scriptkit.command import run
from scriptkit.output import OutputContext, OutputPolicy, TableData


def summarize(amounts: list[int]) -> dict[str, int]:
    """Business logic has no presentation, provider, or manager dependency."""
    return {"count": len(amounts), "total": sum(amounts)}


def main() -> int:
    context = OutputContext(OutputPolicy(machine="--json" in sys.argv))

    def execute() -> object:
        with context.progress("Summing invoices", total=3) as task:
            if "--cancel" in sys.argv:
                raise KeyboardInterrupt  # run emits cancellation and returns 130
            if "--fail" in sys.argv:
                raise ValueError("fixture failure")
            result = summarize([10, 20, 30])
            task.advance(3)
        table = TableData(("Count", "Total"), ((str(result["count"]), str(result["total"])),))
        if context.policy.machine:
            return {"summary": result, "table": table.to_data()}
        context.table(table)
        return None

    return run(context, execute)


if __name__ == "__main__":
    raise SystemExit(main())
