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
        return out

    def concrete_targets(self, owner: str, name: str, desc: str) -> list[tuple[int, str]]:
        """Return list of (concrete_class_id, impl_owner) for dispatch."""
        if owner not in self.classes:
            return []
        out: list[tuple[int, str]] = []
        for sub in self._subtypes(owner):
            ci = self.classes[sub]
            if ci.cf.is_interface or (ci.cf.access & ACC_ABSTRACT):
                continue
            if self.instantiated is not None and sub not in self.instantiated:
                continue
            impl = self._find_impl(sub, name, desc)
            if impl is not None:
                out.append((ci.class_id, impl))
        out.sort()
        return out

    def _find_impl(self, concrete_cls: str, name: str, desc: str) -> str | None:
        cur: str | None = concrete_cls
        while cur and cur in self.classes:
            cci = self.classes[cur]
            if any(
                not mm.is_static()
                and not (mm.access & ACC_ABSTRACT)
                and mm.name == name
                and mm.descriptor == desc
                for mm in cci.cf.methods
            ):
                return cur
            cur = cci.cf.super_name
        # Default interface method fallback
        stk = list(self.classes[concrete_cls].all_interfaces)
        seen: set[str] = set()
        while stk:
            ic = stk.pop()
            if ic in seen or ic not in self.classes:
                continue
            seen.add(ic)
            ici = self.classes[ic]
            if any(
                not mm.is_static()
                and not (mm.access & ACC_ABSTRACT)
                and mm.name == name
                and mm.descriptor == desc
                for mm in ici.cf.methods
            ):
                return ic
            stk.extend(ici.cf.interfaces)
        return None

    def resolve_method(self, owner: str, name: str, desc: str) -> str | None:
        """Resolve a declared/inherited method (including static/special) up the hierarchy."""
        cur: str | None = owner
        while cur and cur in self.classes:
            cci = self.classes[cur]
            if any(mm.name == name and mm.descriptor == desc and mm.code is not None for mm in cci.cf.methods):
                return cur
            cur = cci.cf.super_name
        # Check superinterfaces for default methods
        if owner in self.classes:
            stk = list(self.classes[owner].all_interfaces)
            seen: set[str] = set()
            while stk:
                ic = stk.pop()
                if ic in seen or ic not in self.classes:
                    continue
                seen.add(ic)
                ici = self.classes[ic]
                if any(mm.name == name and mm.descriptor == desc and mm.code is not None for mm in ici.cf.methods):
                    return ic
                stk.extend(ici.cf.interfaces)
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
