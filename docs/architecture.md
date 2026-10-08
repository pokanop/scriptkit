# Ownership and dependency direction

Authoritative framework repository: https://github.com/pokanop/scriptkit.
`pokanop/scripts` remains the consumer; migration is separate work (POK-628).

| Component / path | Ownership / dependency rule |
| --- | --- |
| Runtime: `src/scriptkit/{app,blocks,cli,config,console,doctor,proc,progress,style,tables,text}.py`, `__init__.py` | Existing 1.3.0 API, dependency-light, never imports manager/registry/generator/AI. |
| Composition root: `entrypoint.py`, `__main__.py` | Typed `main(argv: Sequence[str] \| None) -> int` injectable argument boundary. Help/version only. Do not put business logic here. |
| Contracts: future `scriptkit.contracts` (POK-617) | Versioned typed records/protocols shared by upper layers; depends only on stdlib. Not implemented in this extraction. |
| Manager: future `scriptkit.manager` (POK-621/622) | Transaction orchestration; inject registry/platform adapters, not global services. |
| Registry: future `scriptkit.registry` (POK-620) | Resolution, verification/cache adapters; no CLI or AI dependency. |
| Generator: future `scriptkit.generator` (POK-623/624) | Deterministic spec-to-files logic, independent from manager and AI. |
| AI: future `scriptkit.ai` (POK-626/627) | Optional proposal/provider adapters; calls generator contracts, never imported by runtime. |

Only the runtime and typed CLI composition boundary exist today. Do not create
placeholder implementations or speculative protocol APIs for later tickets.
Existing typed result records (`proc.Result`, `doctor.Check`, `blocks.ManagedBlock`)
remain intact. Preserve runtime signatures during extraction; full protocol/schema
ownership and type-check expansion belong to POK-617. We do not yet declare the
whole inherited library PEP 561-complete (`py.typed`).

## Template resources

No templates or consumer-specific `template` scripts ship in this slice.
POK-624 owns `scriptkit.generator.templates` as an importable resource package,
with immutable versioned directories and a manifest recording template/schema
versions and checksums. Load text via `importlib.resources.files()`; use
`as_file()` only for adapters requiring a temporary filesystem path. Never
resolve templates relative to cwd, a Git checkout or `__file__` parents.
When introduced, explicitly include resources in wheel/sdist and exercise them
from installed artifacts outside the source tree. No runtime network fetch or
AI generation may replace a missing bundled template.
