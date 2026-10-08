"""Contract tests also run against the installed wheel, outside the checkout."""

from dataclasses import FrozenInstanceError, replace
import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator
import pytest

from scriptkit.contracts import (
    AIProposal,
    ArgumentSpec,
    Artifact,
    CatalogRelease,
    CommandSpec,
    CONTRACTS,
    ContractError,
    DependencyLock,
    Generation,
    InstallPlan,
    LockedPackage,
    Platform,
    PythonRequirement,
    Receipt,
    ToolRelease,
    ToolSpec,
    resource_text,
)


def example(cls):
    return cls.from_json(resource_text(f"{cls.__name__}.example.json"))


@pytest.mark.parametrize("cls", CONTRACTS)
def test_golden_roundtrip_and_schema(cls):
    text = resource_text(f"{cls.__name__}.example.json")
    value = cls.from_json(text)
    assert value.canonical_json() + "\n" == text
    assert cls.from_dict(value.to_dict()) == value
    schema = json.loads(resource_text(f"{cls.__name__}.schema.json"))
    assert schema == cls.json_schema(), "regenerate resources with tools/export_contracts.py"
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(value.to_dict())
    reversed_keys = dict(reversed(list(value.to_dict().items())))
    assert cls.from_dict(reversed_keys).canonical_json() == value.canonical_json()
    with pytest.raises(FrozenInstanceError):
        value.schema_version = 2


@pytest.mark.parametrize("cls", CONTRACTS)
@pytest.mark.parametrize("version", [0, 2, "1", True, 1.0, None])
def test_reject_unknown_or_malformed_schema_versions(cls, version):
    data = example(cls).to_dict()
    data["schema_version"] = version
    with pytest.raises(ContractError, match="schema_version"):
        cls.from_dict(data)


@pytest.mark.parametrize("cls", CONTRACTS)
def test_no_hooks_or_silent_field_dropping(cls):
    data = example(cls).to_dict()
    data["post_install"] = "echo unsafe"
    with pytest.raises(ContractError, match="unknown fields.*post_install"):
        cls.from_dict(data)
    del data["post_install"]
    del data["schema_version"]
    with pytest.raises(ContractError, match="missing fields.*schema_version"):
        cls.from_dict(data)


@pytest.mark.parametrize(
    "text", ["{} trailing", '{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}', "[]"]
)
def test_invalid_json(text):
    with pytest.raises(ContractError):
        ToolSpec.from_json(text)


@pytest.mark.parametrize(
    "name",
    [
        "../bad",
        "bad/name",
        "-flag",
        "CON",
        "con",
        "lpt1",
        "name.",
        "",
        "two words",
        "x\n",
        "é",
        "x" * 65,
    ],
)
def test_unsafe_names(name):
    with pytest.raises(ContractError, match="name"):
        replace(example(ToolSpec), name=name)


@pytest.mark.parametrize(
    "path",
    [
        "/absolute",
        "../escape",
        "a/../b",
        "a/./b",
        "C:/x",
        "a\\b",
        "a//b",
        "a/",
        "a/con.txt",
        "nul",
        "a.",
        "a/b ",
        "a\x00b",
        "a\nb",
        "a\n",
        "a:b",
        "a/COM1",
        "a/..",
    ],
)
def test_unsafe_paths(path):
    with pytest.raises(ContractError, match="path"):
        Artifact(path, "a" * 64, 0)


@pytest.mark.parametrize(
    "version", ["1", "v1.2.3", "01.2.3", "1.2.3-01", "1.2.3+", "1.2.3\n", "1.2.3;rm"]
)
def test_invalid_semver(version):
    with pytest.raises(ContractError, match="version"):
        replace(example(ToolSpec), version=version)


@pytest.mark.parametrize(
    "version", ["0.0.0", "1.2.3", "1.2.3-rc.1+build.42", "1.2.3-0", "1.2.3-01a"]
)
def test_semver(version):
    assert replace(example(ToolSpec), version=version).version == version


@pytest.mark.parametrize(
    "digest", ["a" * 63, "a" * 65, "A" * 64, "z" * 64, "sha256:" + "a" * 64, "a" * 64 + "\n"]
)
def test_invalid_digest(digest):
    with pytest.raises(ContractError, match="sha256"):
        Artifact("safe.zip", digest, 0)


def test_strict_primitive_types_and_immutability():
    with pytest.raises(ContractError, match="size"):
        Artifact("x", "a" * 64, True)
    with pytest.raises(ContractError, match="size"):
        Artifact("x", "a" * 64, -1)
    with pytest.raises(ContractError, match="tuple"):
        replace(example(ToolSpec), platforms=[Platform("linux", "x86_64")])
    with pytest.raises(ContractError, match="expected array"):
        data = example(ToolSpec).to_dict()
        data["platforms"] = ()
        ToolSpec.from_dict(data)
    for kind, default in [("string", "yes"), ("integer", 42), ("boolean", True)]:
        arg = ArgumentSpec(
            1, "flag", "flag" if kind == "boolean" else "option", kind, False, default, (), ""
        )
        assert ArgumentSpec.from_json(arg.canonical_json()) == arg


@pytest.mark.parametrize(
    "minimum, maximum",
    [
        ("3.10.0", "3.15.0"),
        ("3.12.0", "3.11.0"),
        ("3.11.0", "3.11.0"),
        ("3.11", "3.15.0"),
        ("3.011.0", "3.15.0"),
    ],
)
def test_contradictory_python(minimum, maximum):
    with pytest.raises(ContractError):
        PythonRequirement(minimum, maximum)


def test_cross_field_constraints():
    tool, lock, plan = example(ToolSpec), example(DependencyLock), example(InstallPlan)
    with pytest.raises(ContractError, match="duplicate"):
        replace(tool, platforms=tool.platforms * 2)
    with pytest.raises(ContractError, match="duplicate"):
        replace(tool, commands=tool.commands * 2)
    with pytest.raises(ContractError, match="backend"):
        replace(lock, backend="winget")
    with pytest.raises(ContractError, match="cover exactly"):
        ToolRelease(
            tool, plan.release.artifact, (replace(lock, platform=Platform("macos", "arm64")),)
        )
    with pytest.raises(ContractError, match="subset"):
        ToolRelease(
            tool,
            plan.release.artifact,
            (replace(lock, python=PythonRequirement("3.11.0", "3.16.0")),),
        )
    with pytest.raises(ContractError, match="python_version"):
        replace(plan, python_version="3.15.0")
    with pytest.raises(ContractError, match="platform"):
        replace(plan, platform=Platform("windows", "x86_64"))
    with pytest.raises(ContractError, match="backends"):
        replace(plan, backends=("apt",))
    with pytest.raises(ContractError, match="previous_generation"):
        replace(plan, previous_generation=plan.generation)
    with pytest.raises(ContractError, match="lineage"):
        replace(example(Generation), name="generation-2")
    with pytest.raises(ContractError, match="parent directory"):
        replace(example(Receipt), files=(Artifact("a", "a" * 64, 1), Artifact("a/b", "b" * 64, 1)))
    with pytest.raises(ContractError, match="expected module"):
        replace(tool, entrypoint="python -c 'bad()'")
    package = LockedPackage("sample", "1.0.0", Artifact("sample.whl", "a" * 64, 1))
    with pytest.raises(ContractError, match="duplicate"):
        replace(lock, packages=(package, package))


@pytest.mark.parametrize(
    "changes",
    [
        dict(default=1),
        dict(default="x"),
        dict(kind="flag"),
        dict(value_type="boolean"),
        dict(choices=("x", "x")),
    ],
)
def test_argument_contradictions(changes):
    with pytest.raises(ContractError):
        replace(example(ArgumentSpec), **changes)


def test_remaining_structural_and_cross_field_errors():
    with pytest.raises(ContractError, match="expected array"):
        data = example(ToolSpec).to_dict()
        data["commands"] = "bad"
        ToolSpec.from_dict(data)
    with pytest.raises(ContractError, match="platform: os"):
        data = example(DependencyLock).to_dict()
        data["platform"]["os"] = "freebsd"
        DependencyLock.from_dict(data)
    with pytest.raises(ContractError, match="allowed type"):
        data = example(ArgumentSpec).to_dict()
        data["default"] = []
        ArgumentSpec.from_dict(data)
    with pytest.raises(ContractError, match="allowed types"):
        replace(example(InstallPlan), previous_generation="../bad")
    with pytest.raises(ContractError, match="choices: only supported"):
        ArgumentSpec(1, "count", "option", "integer", False, 1, ("one",), "")
    with pytest.raises(ContractError, match="default: must be one"):
        ArgumentSpec(1, "target", "option", "string", False, "a", ("b",), "")
    with pytest.raises(ContractError, match="previous: must differ"):
        replace(example(Generation), previous="generation-1")


def test_positional_order():
    arg = example(ArgumentSpec)
    with pytest.raises(ContractError, match="required positional"):
        replace(
            example(CommandSpec), arguments=(replace(arg, name="optional", required=False), arg)
        )


def test_migration_fixtures():
    # v1 is the first published schema. Its bytes remain a permanent compatibility fixture.
    fixture = Path(__file__).with_name("fixtures") / "contracts"
    baseline = (fixture / "tool-v1.json").read_text(encoding="utf-8")
    assert ToolSpec.from_json(baseline).canonical_json() + "\n" == baseline
    for name in ("tool-v0-unsupported.json", "tool-v2-unsupported.json"):
        with pytest.raises(ContractError, match="schema_version"):
            ToolSpec.from_json((fixture / name).read_text(encoding="utf-8"))


def test_disjoint_paths_with_intervening_sort_entry():
    with pytest.raises(ContractError, match="parent directory"):
        replace(example(Receipt), files=tuple(Artifact(p, "a" * 64, 1) for p in ("a", "a-", "a/b")))
    original = example(Generation)
    first = original.receipts[0]
    second = replace(
        first,
        plan=replace(
            first.plan,
            destination="tools/example-tool/nested",
            release=replace(
                first.plan.release, tool=replace(first.plan.release.tool, name="second-tool")
            ),
        ),
    )
    with pytest.raises(ContractError, match="parent directory"):
        replace(original, receipts=(first, second))


@pytest.mark.parametrize("version", ["latest", "HEAD", ">=1.0", "1.*", "1.0;echo", "1.0\n"])
def test_locked_versions_are_not_ranges_or_moving_tags(version):
    with pytest.raises(ContractError, match="version"):
        LockedPackage("microsoft.visualstudiocode", version, Artifact("package.zip", "a" * 64, 1))


def test_backend_python_intersection():
    release = example(InstallPlan).release
    pip = replace(release.locks[0], python=PythonRequirement("3.11.0", "3.12.0"))
    apt = replace(pip, backend="apt", python=PythonRequirement("3.12.0", "3.15.0"))
    with pytest.raises(ContractError, match="no common Python version"):
        replace(release, locks=(pip, apt))
    narrowed = replace(release, locks=(replace(pip, python=PythonRequirement("3.12.0", "3.15.0")),))
    with pytest.raises(ContractError, match="outside dependency lock"):
        replace(example(InstallPlan), release=narrowed)


def test_resource_path_confined():
    for name in ("../ToolSpec.example.json", "/tmp/x", "ToolSpec.json"):
        with pytest.raises(ContractError, match="resource"):
            resource_text(name)


def test_offline_injected_ports(tmp_path):
    """Local artifact catalog and fake ports: no production credentials or services."""
    from scriptkit.contracts import (
        ArtifactSource,
        Clock,
        FileSystem,
        Output,
        PackageBackend,
        ProposalProvider,
    )

    payload = b"print('offline')\n"
    artifact = Artifact("example.py", hashlib.sha256(payload).hexdigest(), len(payload))
    (tmp_path / artifact.path).write_bytes(payload)
    (tmp_path / "catalog.json").write_text(example(CatalogRelease).canonical_json())

    class LocalCatalog:
        def catalog(self, name: str, version: str) -> CatalogRelease:
            value = CatalogRelease.from_json((tmp_path / "catalog.json").read_text())
            if (value.name, value.version) != (name, version):
                raise FileNotFoundError(name)
            return value

        def fetch(self, artifact: Artifact) -> bytes:
            data = (tmp_path / artifact.path).read_bytes()
            if len(data) != artifact.size or hashlib.sha256(data).hexdigest() != artifact.sha256:
                raise ContractError("artifact integrity mismatch")
            return data

    class FakeBackend:
        interrupted = False

        def available(self, lock: DependencyLock) -> bool:
            return lock.backend == "pip"

        def install(self, lock: DependencyLock, destination: str) -> tuple[Artifact, ...]:
            if self.interrupted:
                raise KeyboardInterrupt
            return (artifact,)

    class FakeFS:
        def __init__(self):
            self.files = {}

        def read_bytes(self, path: str) -> bytes:
            return self.files[path]

        def write_atomic(self, path: str, data: bytes) -> None:
            self.files[path] = data

        def exists(self, path: str) -> bool:
            return path in self.files

        def remove_tree(self, path: str) -> None:
            for key in list(self.files):
                if key == path or key.startswith(path + "/"):
                    del self.files[key]

        def activate(self, staged: str, active: str) -> None:
            self.files[active] = self.files[staged]

    class FakeClock:
        def unix_seconds(self) -> int:
            return 1700000000

    class FakeOutput:
        def emit(self, level: str, message: str) -> None:
            events.append((level, message))

    class FakeProvider:
        def propose(self, request_id: str, prompt: str, base: ToolSpec | None) -> AIProposal:
            return replace(example(AIProposal), request_id=request_id)

    source: ArtifactSource = LocalCatalog()
    backend: PackageBackend = FakeBackend()
    fs: FileSystem = FakeFS()
    clock: Clock = FakeClock()
    events = []
    output: Output = FakeOutput()
    provider: ProposalProvider = FakeProvider()
    assert source.catalog("local-catalog", "1.0.0") == example(CatalogRelease)
    fs.write_atomic("stage/example.py", source.fetch(artifact))
    assert fs.read_bytes("stage/example.py") == payload
    assert backend.available(example(DependencyLock))
    receipt = Receipt(
        1,
        example(InstallPlan),
        clock.unix_seconds(),
        backend.install(example(DependencyLock), "stage"),
    )
    fs.write_atomic("stage/receipt.json", receipt.canonical_json().encode())
    fs.activate("stage/receipt.json", "active")
    assert Receipt.from_json(fs.read_bytes("active").decode()) == receipt
    assert provider.propose("request-2", "offline", None).request_id == "request-2"
    output.emit("info", "installed")
    assert events == [("info", "installed")]
    fs.remove_tree("stage")
    assert fs.exists("active") and not fs.exists("stage/example.py")
    with pytest.raises(ContractError, match="integrity"):
        source.fetch(replace(artifact, sha256="0" * 64))
    with pytest.raises(FileNotFoundError):
        source.catalog("missing", "1.0.0")
    backend.interrupted = True
    with pytest.raises(KeyboardInterrupt):
        backend.install(example(DependencyLock), "stage")
    assert Receipt.from_json(fs.read_bytes("active").decode()) == receipt
