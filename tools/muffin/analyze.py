"""
Static analysis of a jar: "how big is the mountain and what is it made of?"

Produces the numbers that drive Muffin's design decisions (see docs/RESEARCH.md):
which JDK / library APIs must exist in the Mojo runtime, how many lambdas,
how much synchronization, how much reflection, etc.
"""

from __future__ import annotations

import json
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .bytecode import decode
from .classfile import ACC_ABSTRACT, ACC_NATIVE, ACC_SYNCHRONIZED, ACC_VOLATILE, parse_class

BOOTSTRAP_KINDS = {
    "java/lang/invoke/LambdaMetafactory": "lambda",
    "java/lang/invoke/StringConcatFactory": "string_concat",
    "java/lang/runtime/ObjectMethods": "record_methods",
    "java/lang/runtime/SwitchBootstraps": "pattern_switch",
}

CONCURRENCY_PREFIXES = ("java/util/concurrent/", "java/lang/Thread", "java/util/concurrent/locks/")
REFLECTION_PREFIXES = ("java/lang/reflect/", "java/lang/invoke/MethodHandle", "java/lang/invoke/VarHandle")


def _pkg(name: str, depth: int) -> str:
    parts = name.split("/")
    return "/".join(parts[: min(depth, len(parts) - 1)]) or "<default>"


@dataclass
class Report:
    jar: str
    classes: int = 0
    interfaces: int = 0
    enums: int = 0
    records: int = 0
    abstract_classes: int = 0
    sealed: int = 0
    class_versions: Counter = field(default_factory=Counter)
    methods: int = 0
    abstract_methods: int = 0
    native_methods: int = 0
    synchronized_methods: int = 0
    fields: int = 0
    volatile_fields: int = 0
    static_fields: int = 0
    bytecode_bytes: int = 0
    instructions: int = 0
    opcodes: Counter = field(default_factory=Counter)
    exception_handlers: int = 0
    indy: Counter = field(default_factory=Counter)
    max_method_bytes: tuple[int, str] = (0, "")
    packages: Counter = field(default_factory=Counter)
    external_owners: Counter = field(default_factory=Counter)
    external_packages: Counter = field(default_factory=Counter)
    concurrency_refs: Counter = field(default_factory=Counter)
    reflection_refs: Counter = field(default_factory=Counter)
    resources: int = 0
    resource_bytes: int = 0

    def to_json(self) -> dict:
        d = {}
        for k, v in self.__dict__.items():
            if isinstance(v, Counter):
                d[k] = dict(v.most_common(60))
            else:
                d[k] = v
        return d


def analyze_jar(path: Path) -> Report:
    rep = Report(jar=str(path))
    raw: dict[str, bytes] = {}
    with zipfile.ZipFile(path) as z:
        for info in z.infolist():
            if info.filename.endswith(".class") and not info.filename.startswith("META-INF/"):
                raw[info.filename] = z.read(info)
            elif not info.is_dir():
                rep.resources += 1
                rep.resource_bytes += info.file_size

    parsed = [parse_class(b) for b in raw.values()]
    local = {cf.name for cf in parsed}

    for cf in parsed:
        rep.classes += 1
        rep.class_versions[cf.major] += 1
        rep.packages[_pkg(cf.name, 3)] += 1
        if cf.is_interface:
            rep.interfaces += 1
        elif cf.access & ACC_ABSTRACT:
            rep.abstract_classes += 1
        if cf.is_enum:
            rep.enums += 1
        if cf.is_record:
            rep.records += 1
        if cf.permitted_subclasses:
            rep.sealed += 1

        for f in cf.fields:
            rep.fields += 1
            rep.volatile_fields += bool(f.access & ACC_VOLATILE)
            rep.static_fields += f.is_static()

        for m in cf.methods:
            rep.methods += 1
            rep.native_methods += bool(m.access & ACC_NATIVE)
            rep.synchronized_methods += bool(m.access & ACC_SYNCHRONIZED)
            if m.code is None:
                rep.abstract_methods += not (m.access & ACC_NATIVE)
                continue
            n = len(m.code.code)
            rep.bytecode_bytes += n
            if n > rep.max_method_bytes[0]:
                rep.max_method_bytes = (n, f"{cf.name}.{m.name}{m.descriptor}")
            rep.exception_handlers += len(m.code.handlers)
            for insn in decode(m.code.code, cf.cp):
                rep.instructions += 1
                rep.opcodes[insn.op] += 1
                if insn.op == "invokedynamic":
                    bsm = cf.bootstrap_methods[insn.args[0]]
                    owner = bsm.handle[1][0]
                    rep.indy[BOOTSTRAP_KINDS.get(owner, owner)] += 1
                    continue
                if not insn.args or not isinstance(insn.args[0], tuple):
                    continue
                ref = insn.args[0]
                if len(ref) == 3 and isinstance(ref[0], str) and insn.op.startswith(("invoke", "get", "put")):
                    owner = ref[0]
                    if owner.startswith("["):
                        continue
                    if owner not in local:
                        rep.external_owners[owner] += 1
                        rep.external_packages[_pkg(owner, 3)] += 1
                    if owner.startswith(CONCURRENCY_PREFIXES):
                        rep.concurrency_refs[f"{owner}.{ref[1]}"] += 1
                    if owner.startswith(REFLECTION_PREFIXES) or (
                        owner == "java/lang/Class" and ref[1] in ("forName", "getDeclaredField", "getDeclaredMethod", "newInstance", "getDeclaredConstructor")
                    ):
                        rep.reflection_refs[f"{owner}.{ref[1]}"] += 1
    return rep


def render_markdown(rep: Report) -> str:
    L: list[str] = []
    w = L.append
    w(f"# Muffin analysis: `{Path(rep.jar).name}`\n")
    w("| metric | value |\n|---|---|")
    for k in (
        "classes", "interfaces", "abstract_classes", "enums", "records", "sealed",
        "methods", "abstract_methods", "native_methods", "synchronized_methods",
        "fields", "static_fields", "volatile_fields",
        "bytecode_bytes", "instructions", "exception_handlers", "resources", "resource_bytes",
    ):
        w(f"| {k} | {getattr(rep, k):,} |")
    w(f"| largest method | {rep.max_method_bytes[0]:,} B `{rep.max_method_bytes[1]}` |")
    w(f"| class-file versions | {dict(rep.class_versions)} |")
    sections = [
        ("invokedynamic bootstraps", rep.indy, 20),
        ("Top packages in jar", rep.packages, 25),
        ("External packages referenced (must be provided by runtime)", rep.external_packages, 40),
        ("Top external classes", rep.external_owners, 40),
        ("Concurrency API usage", rep.concurrency_refs, 30),
        ("Reflection / MethodHandle usage", rep.reflection_refs, 30),
        ("Opcode histogram (top 40)", rep.opcodes, 40),
    ]
    for title, ctr, n in sections:
        w(f"\n## {title}\n")
        w("| name | count |\n|---|---|")
        for name, c in ctr.most_common(n):
            w(f"| `{name}` | {c:,} |")
    return "\n".join(L) + "\n"


def run(jar: Path, out_dir: Path) -> Report:
    rep = analyze_jar(jar)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = jar.stem
    (out_dir / f"{stem}.json").write_text(json.dumps(rep.to_json(), indent=2))
    (out_dir / f"{stem}.md").write_text(render_markdown(rep))
    return rep
