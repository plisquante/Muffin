"""
Whole-program Class Hierarchy Analysis (CHA), O(1) subtype intervals +
interface membership sets, and closed-world devirtualization planner.

Improvements over naive vtable/itable dispatch:
1. Class `instanceof` / `checkcast` compiles to a 2-compare DFS interval
   `lo <= cid <= hi`.
2. Interface `instanceof` / `checkcast` uses precomputed concrete `class_id`
   membership (`interface_impls[iface]`) — no runtime linear itable scan.
3. Closed-world devirtualization (optionally tightened by RTA `instantiated`)
   converts single-target `invokevirtual` / `invokeinterface` sites into
   direct static calls that LLVM can inline across classes.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .classfile import ACC_ABSTRACT, ClassFile


@dataclass(slots=True)
class FieldLayout:
    owner: str
    name: str
    descriptor: str
    mojo_name: str
    is_static: bool


@dataclass(slots=True)
class ClassInfo:
    cf: ClassFile
    class_id: int = -1
    max_sub_id: int = -1
    super_id: int = -1
    all_fields: list[FieldLayout] = field(default_factory=list)
    field_by_name: dict[str, FieldLayout] = field(default_factory=dict)
    static_fields: list[FieldLayout] = field(default_factory=list)
    all_interfaces: set[str] = field(default_factory=set)
    vtable: list[tuple[str, str, str, str]] = field(default_factory=list)
    vtable_index: dict[tuple[str, str], int] = field(default_factory=dict)


@dataclass
class Hierarchy:
    classes: dict[str, ClassInfo]
    children: dict[str, list[str]]
    implementors: dict[str, list[str]]
    interface_impl_ids: dict[str, list[int]] = field(default_factory=dict)
    instantiated: set[str] | None = None
    devirt_cache: dict[tuple[str, str, str], str | None] = field(default_factory=dict)
    subtypes_cache: dict[str, list[str]] = field(default_factory=dict)
    concrete_cache: dict[tuple[str, str, str], list[tuple[int, str]]] = field(default_factory=dict)
    resolve_cache: dict[tuple[str, str, str], str | None] = field(default_factory=dict)
    _class_methods_with_code: dict[str, set[tuple[str, str]]] = field(default_factory=dict)

    @classmethod
    def build(
        cls,
        class_files: list[ClassFile],
        *,
        instantiated: set[str] | None = None,
    ) -> "Hierarchy":
        classes = {cf.name: ClassInfo(cf=cf) for cf in class_files}
        children: dict[str, list[str]] = defaultdict(list)
        implementors: dict[str, list[str]] = defaultdict(list)

        for cf in class_files:
            if cf.super_name and not cf.is_interface:
                children[cf.super_name].append(cf.name)
            for iface in cf.interfaces:
                implementors[iface].append(cf.name)

        h = cls(
            classes=classes,
            children=children,
            implementors=implementors,
            instantiated=instantiated,
        )
        h._assign_dfs_ids()
        h._compute_layouts()
        h._compute_interface_impl_ids()
        for cname, ci in classes.items():
            h._class_methods_with_code[cname] = {
                (mm.name, mm.descriptor) for mm in ci.cf.methods if mm.code is not None
            }
        return h

    def _assign_dfs_ids(self) -> None:
        next_id = 10  # 0..9 reserved for runtime builtins
        roots = [
            name
            for name, ci in self.classes.items()
            if not ci.cf.is_interface and (ci.cf.super_name not in self.classes)
        ]

        def visit(name: str, super_id: int) -> None:
            nonlocal next_id
            ci = self.classes[name]
            ci.class_id = next_id
            ci.super_id = super_id
            next_id += 1
            for child in sorted(self.children.get(name, ())):
                visit(child, ci.class_id)
            ci.max_sub_id = next_id - 1

        for r in sorted(roots):
            visit(r, -1)

    def _compute_layouts(self) -> None:
        visited: set[str] = set()

        def build_one(name: str) -> None:
            if name in visited or name not in self.classes:
                return
            visited.add(name)
            ci = self.classes[name]
            sup = ci.cf.super_name
            inst_fields: list[FieldLayout] = []
            vtable: list[tuple[str, str, str, str]] = []
            vmap: dict[tuple[str, str], int] = {}
            all_ifaces: set[str] = set(ci.cf.interfaces)

            if sup and sup in self.classes:
                build_one(sup)
                sci = self.classes[sup]
                inst_fields.extend(sci.all_fields)
                vtable.extend(sci.vtable)
                vmap.update(sci.vtable_index)
                all_ifaces.update(sci.all_interfaces)

            for iface in list(ci.cf.interfaces):
                if iface in self.classes:
                    build_one(iface)
                    all_ifaces.update(self.classes[iface].all_interfaces)

            used_names = {fl.mojo_name for fl in inst_fields}
            for f in ci.cf.fields:
                fl_name = f.name
                if fl_name in used_names:
                    fl_name = f"{fl_name}_{ci.class_id}"
                used_names.add(fl_name)
                fl = FieldLayout(name, f.name, f.descriptor, fl_name, f.is_static())
                if f.is_static():
                    ci.static_fields.append(fl)
                else:
                    inst_fields.append(fl)

            for m in ci.cf.methods:
                if m.is_static() or m.name in ("<init>", "<clinit>"):
                    continue
                if m.access & ACC_ABSTRACT:
                    continue
                key = (m.name, m.descriptor)
                if key in vmap:
                    vtable[vmap[key]] = (m.name, m.descriptor, name, m.descriptor)
                else:
                    vmap[key] = len(vtable)
                    vtable.append((m.name, m.descriptor, name, m.descriptor))

            ci.all_fields = inst_fields
            ci.field_by_name = {fl.name: fl for fl in inst_fields}
            ci.vtable = vtable
            ci.vtable_index = vmap
            ci.all_interfaces = all_ifaces

        for name in self.classes:
            build_one(name)

    def _compute_interface_impl_ids(self) -> None:
        impls: dict[str, list[int]] = defaultdict(list)
        for name, ci in self.classes.items():
            if ci.cf.is_interface or (ci.cf.access & ACC_ABSTRACT):
                continue
            for iface in ci.all_interfaces:
                impls[iface].append(ci.class_id)
        for k in impls:
            impls[k].sort()
        self.interface_impl_ids = dict(impls)

    def _subtypes(self, root: str) -> list[str]:
        cached = self.subtypes_cache.get(root)
        if cached is not None:
            return cached
        out: list[str] = []
        stack = [root]
        seen: set[str] = set()
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            if cur in self.classes:
                out.append(cur)
            stack.extend(self.children.get(cur, ()))
            stack.extend(self.implementors.get(cur, ()))
        self.subtypes_cache[root] = out
        return out

    def concrete_targets(self, owner: str, name: str, desc: str) -> list[tuple[int, str]]:
        """Return list of (concrete_class_id, impl_owner) for dispatch."""
        if owner not in self.classes:
            return []
        key = (owner, name, desc)
        cached = self.concrete_cache.get(key)
        if cached is not None:
            return cached
        out: list[tuple[int, str]] = []
        inst = self.instantiated
        for sub in self._subtypes(owner):
            ci = self.classes[sub]
            if ci.cf.is_interface or (ci.cf.access & ACC_ABSTRACT):
                continue
            if inst is not None and sub not in inst:
                continue
            impl = self._find_impl(sub, name, desc)
            if impl is not None:
                out.append((ci.class_id, impl))
        out.sort()
        self.concrete_cache[key] = out
        return out

    def _find_impl(self, concrete_cls: str, name: str, desc: str) -> str | None:
        ci = self.classes[concrete_cls]
        slot = ci.vtable_index.get((name, desc))
        if slot is not None:
            return ci.vtable[slot][2]
        # Default interface method fallback
        stk = list(ci.all_interfaces)
        seen: set[str] = set()
        while stk:
            ic = stk.pop()
            if ic in seen or ic not in self.classes:
                continue
            seen.add(ic)
            ici = self.classes[ic]
            if (name, desc) in ici.vtable_index:
                return ici.vtable[ici.vtable_index[(name, desc)]][2]
            stk.extend(ici.cf.interfaces)
        return None

    def resolve_method(self, owner: str, name: str, desc: str) -> str | None:
        """Resolve a declared/inherited method (including static/special) up the hierarchy."""
        key = (owner, name, desc)
        if key in self.resolve_cache:
            return self.resolve_cache[key]
        mkey = (name, desc)
        cur: str | None = owner
        while cur and cur in self.classes:
            if mkey in self._class_methods_with_code[cur]:
                self.resolve_cache[key] = cur
                return cur
            cur = self.classes[cur].cf.super_name
        # Check superinterfaces for default methods
        if owner in self.classes:
            stk = list(self.classes[owner].all_interfaces)
            seen: set[str] = set()
            while stk:
                ic = stk.pop()
                if ic in seen or ic not in self.classes:
                    continue
                seen.add(ic)
                if mkey in self._class_methods_with_code[ic]:
                    self.resolve_cache[key] = ic
                    return ic
                stk.extend(self.classes[ic].cf.interfaces)
        self.resolve_cache[key] = None
        return None

    def resolve_devirtualized(self, owner: str, name: str, desc: str) -> str | None:
        """If only one concrete implementation exists in `owner`'s cone, return it."""
        key = (owner, name, desc)
        if key in self.devirt_cache:
            return self.devirt_cache[key]

        targets = self.concrete_targets(owner, name, desc)
        impls = {impl for _, impl in targets}
        res = next(iter(impls)) if len(impls) == 1 else None
        self.devirt_cache[key] = res
        return res

    def devirtualization_stats(self) -> tuple[int, int]:
        total = devirt = 0
        for name, ci in self.classes.items():
            for m in ci.cf.methods:
                if m.is_static() or m.name in ("<init>", "<clinit>"):
                    continue
                total += 1
                if self.resolve_devirtualized(name, m.name, m.descriptor) is not None:
                    devirt += 1
        return total, devirt
