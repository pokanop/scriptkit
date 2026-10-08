# ScriptKit (working name)

A dependency-light Python **3.11+** runtime for cohesive command-line tools.
This repository currently ships the compatible 1.3.0 runtime, not the planned
installer, manager, registry, generator or AI product.

> The working name overlaps with [johnlindquist/kit](https://github.com/johnlindquist/kit),
> a separate project. No affiliation is implied. `pokanop-scriptkit` is provisional
> build metadata, **not a claimed available PyPI name**. Namespace/brand review
> must precede public package publication. No release/upload workflow is enabled.

## Build and try

```sh
python -m venv .venv
# Activate .venv using your shell's platform-specific activation command.
python -m pip install '.[dev]'
python -m build
python -m pip install dist/pokanop_scriptkit-1.3.0-py3-none-any.whl
scriptkit --version
python -m scriptkit --help
```

The `scriptkit` command currently provides help/version only. Unknown commands
fail with argparse exit 2 rather than pretending that a manager exists.

```python
import scriptkit as sk

sk.success("Ready")
result = sk.run(["git", "status", "--short"])
print(result.out)
```

There are **no mandatory runtime dependencies**, including no `requests`.
Install `.[rich]` from a checkout for optional Rich 13 rendering; without it,
plain-text output, progress, tables and diagnostics still work.

See [runtime API](docs/runtime.md), [component ownership](docs/architecture.md),
[compatibility policy](docs/compatibility.md), [contributing](CONTRIBUTING.md)
and [security](SECURITY.md). MIT; attribution and extraction provenance are in
[NOTICE.md](NOTICE.md).
