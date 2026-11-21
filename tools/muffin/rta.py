"""
Whole-program Rapid Type Analysis (RTA) over ClassPath + IndyDesugarer.

Starting from an entry class (`main([Ljava/lang/String;)V`), `<clinit>` hooks,
and `META-INF/services` registrations, RTA computes the exact closed-world
reachable subset of:
  - loaded classes & instantiated concrete classes
  - reachable methods (resolving virtual/interface calls ONLY to classes that
    can actually be instantiated)
  - missing external classes & native methods (for gap reporting)
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from .bytecode import decode
from .classfile import ACC_ABSTRACT, ACC_NATIVE, ClassFile, Member
from .classpath import ClassPath
from .indy import IndyDesugarer
from .types import parse_method_desc


@dataclass
class RTAResult:
    loaded: dict[str, ClassFile]
    instantiated: set[str]
    reachable_methods: set[tuple[str, str, str]]
    virtual_sites: set[tuple[str, str, str]]
    missing_classes: set[str]
    native_methods: set[tuple[str, str, str]]
    unsupported_indy: list[str]
    services: dict[str, list[str]] = field(default_factory=dict)


def _find_method(
    loaded: dict[str, ClassFile], owner: str, name: str, desc: str
) -> tuple[str, Member] | None:
    cur: str | None = owner
    while cur and cur in loaded:
        cf = loaded[cur]
        for m in cf.methods:
            if m.name == name and m.descriptor == desc:
                return cur, m
        cur = cf.super_name
    # Fallback: default interface methods
    queue = deque([owner])
    seen: set[str] = set()
    while queue:
        c = queue.popleft()
        if c in seen or c not in loaded:
            continue
        seen.add(c)
        cf = loaded[c]
        for m in cf.methods:
            if m.name == name and m.descriptor == desc and not (m.access & ACC_ABSTRACT):
                return c, m
        if cf.super_name:
            queue.append(cf.super_name)
        queue.extend(cf.interfaces)
    return None


def run_rta(
    cp: ClassPath,
    entry_class: str,
    *,
    extra_roots: list[str] | None = None,
) -> RTAResult:
    des = IndyDesugarer()
    loaded: dict[str, ClassFile] = {}
    missing_classes: set[str] = set()
    instantiated: set[str] = set()
    initialized: set[str] = set()
    reachable_methods: set[tuple[str, str, str]] = set()
    virtual_sites: set[tuple[str, str, str]] = set()
    native_methods: set[tuple[str, str, str]] = set()

    # Subtype & virtual-site indices for O(1) incremental dispatch
    sub_classes: dict[str, set[str]] = {}
    virtual_by_owner: dict[str, list[tuple[str, str]]] = {}
    method_q: deque[tuple[str, str, str]] = deque()

    def load_class(name: str) -> ClassFile | None:
        if name.startswith("["):
            elem = name.lstrip("[")
            if elem.startswith("L") and elem.endswith(";"):
                load_class(elem[1:-1])
            return None
        if name in loaded:
            return loaded[name]
        if name in missing_classes:
            return None
        cf = cp.find(name)
        if cf is None:
            missing_classes.add(name)
            return None
        des.rewrite_class(cf)
        for sc in des.synthetic_classes:
            if sc.name not in loaded:
                loaded[sc.name] = sc
                _register_supers(sc)
        des.synthetic_classes.clear()
        loaded[name] = cf
        _register_supers(cf)
        if cf.super_name:
            load_class(cf.super_name)
        for iface in cf.interfaces:
            load_class(iface)
        init_class(name)
        return cf

    def _register_supers(cf: ClassFile) -> None:
        if cf.super_name:
            sub_classes.setdefault(cf.super_name, set()).add(cf.name)
        for iface in cf.interfaces:
            sub_classes.setdefault(iface, set()).add(cf.name)

    def init_class(name: str) -> None:
        if name in initialized or name not in loaded:
            return
        initialized.add(name)
        cf = loaded[name]
        if cf.super_name:
            load_class(cf.super_name)
        for m in cf.methods:
            if m.name == "<clinit>" and m.descriptor == "()V":
                enqueue_method(name, "<clinit>", "()V")
                break

    def enqueue_method(owner: str, name: str, desc: str) -> None:
        key = (owner, name, desc)
        if key in reachable_methods:
            return
        cf = load_class(owner)
        if cf is None:
            return
        found = _find_method(loaded, owner, name, desc)
        if found is None:
            return
        real_owner, m = found
        rkey = (real_owner, m.name, m.descriptor)
        if rkey in reachable_methods:
            return
        reachable_methods.add(rkey)
        if m.access & ACC_NATIVE:
            native_methods.add(rkey)
        if m.code is not None:
            method_q.append(rkey)

    def mark_instantiated(name: str) -> None:
        if name.startswith("["):
            return
        cf = load_class(name)
        if cf is None or name in instantiated:
            return
        instantiated.add(name)
        ancestors: set[str] = set()
        stk = [name]
        while stk:
            cur = stk.pop()
            if cur in ancestors or cur not in loaded:
                continue
            ancestors.add(cur)
            ccf = loaded[cur]
            if ccf.super_name:
                stk.append(ccf.super_name)
            stk.extend(ccf.interfaces)
        for anc in ancestors:
            for v_name, v_desc in virtual_by_owner.get(anc, ()):
                enqueue_method(name, v_name, v_desc)

    def add_virtual_site(owner: str, name: str, desc: str) -> None:
        load_class(owner)
        vkey = (owner, name, desc)
        if vkey in virtual_sites:
            return
        virtual_sites.add(vkey)
        virtual_by_owner.setdefault(owner, []).append((name, desc))
        stk = [owner]
        seen: set[str] = set()
        while stk:
            cur = stk.pop()
            if cur in seen:
                continue
            seen.add(cur)
            if cur in instantiated:
                enqueue_method(cur, name, desc)
            stk.extend(sub_classes.get(cur, ()))

    # Seed roots
    entry = entry_class.replace(".", "/")
    load_class(entry)
    enqueue_method(entry, "main", "([Ljava/lang/String;)V")
    for r in extra_roots or ():
        load_class(r.replace(".", "/"))
    services = cp.services()
    for svc, providers in services.items():
        load_class(svc)
        for p in providers:
            if cp.find(p) is not None:
                mark_instantiated(p)
                enqueue_method(p, "<init>", "()V")

    # Main fixed-point worklist
    while method_q:
        owner, mname, mdesc = method_q.popleft()
        cf = loaded[owner]
        m = next((mm for mm in cf.methods if mm.name == mname and mm.descriptor == mdesc), None)
        if m is None or m.code is None:
            continue
        insns = (
            m.code.insns_override
            if m.code.insns_override is not None
            else list(decode(m.code.code, cf.cp))
        )
        for ins in insns:
            op = ins.op
            if op == "new":
                mark_instantiated(ins.args[0])
            elif op in ("anewarray", "multianewarray", "checkcast", "instanceof"):
                load_class(ins.args[0])
            elif op in ("getstatic", "putstatic", "getfield", "putfield"):
                f_owner = ins.args[0][0]
                load_class(f_owner)
            elif op == "invokestatic":
                t_owner, t_name, t_desc = ins.args[0]
                enqueue_method(t_owner, t_name, t_desc)
            elif op == "invokespecial":
                t_owner, t_name, t_desc = ins.args[0]
                enqueue_method(t_owner, t_name, t_desc)
            elif op in ("invokevirtual", "invokeinterface"):
                t_owner, t_name, t_desc = ins.args[0]
                add_virtual_site(t_owner, t_name, t_desc)
            elif op in ("ldc", "ldc_w"):
                c = ins.args[0]
                if isinstance(c, tuple) and c[0] == "class":
                    load_class(c[1])

    return RTAResult(
        loaded=loaded,
        instantiated=instantiated,
        reachable_methods=reachable_methods,
        virtual_sites=virtual_sites,
        missing_classes=missing_classes,
        native_methods=native_methods,
        unsupported_indy=des.unsupported,
        services=services,
    )
