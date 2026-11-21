"""
Whole-program Class Hierarchy Analysis (CHA), O(1) subtype intervals, and
closed-world devirtualization planner.

Instead of fat JVM-style vtable/itable lookup chains at runtime:
1. Every concrete & abstract class gets a DFS pre-order interval `[id, max_sub_id]`,
   so `x instanceof C` compiles to `lo <= x.class_id() <= hi` (2 integer compares).
2. Under closed-world assumption, any `invokevirtual` / `invokeinterface` whose
   receiver hierarchy contains only ONE concrete implementation is devirtualized
   to a direct static call (`Devirtualization` pass).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .classfile import ACC_ABSTRACT, ClassFile, Member


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
    all_fields: list[FieldLayout] = field(default_factory=list)  # instance fields in layout order
    static_fields: list[FieldLayout] = field(default_factory=list)
    vtable: list[tuple[str, str, str, str]] = field(default_factory=list)  # (name, desc, impl_owner, impl_desc)
    vtable_index: dict[tuple[str, str], int] = field(default_factory=dict)


@dataclass
class Hierarchy:
    classes: dict[str, ClassInfo]
    children: dict[str, list[str]]
    implementors: dict[str, list[str]]
    # (target_owner, name, desc) -> single concrete impl owner if devirtualizable
    devirt_cache: dict[tuple[str, str, str], str | None] = field(default_factory=dict)

    @classmethod
    def build(cls, class_files: list[ClassFile]) -> "Hierarchy":
        classes = {cf.name: ClassInfo(cf=cf) for cf in class_files}
        children: dict[str, list[str]] = defaultdict(list)
        implementors: dict[str, list[str]] = defaultdict(list)

        for cf in class_files:
            if cf.super_name and not cf.is_interface:
                children[cf.super_name].append(cf.name)
            for iface in cf.interfaces:
                implementors[iface].append(cf.name)

        h = cls(classes=classes, children=children, implementors=implementors)
        h._assign_dfs_ids()
        h._compute_layouts()
        return h

    def _assign_dfs_ids(self) -> None:
        next_id = 10  # 0..9 reserved for runtime builtins (JString, primitives, arrays)
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

            if sup and sup in self.classes:
                build_one(sup)
                sci = self.classes[sup]
                inst_fields.extend(sci.all_fields)
                vtable.extend(sci.vtable)
                vmap.update(sci.vtable_index)

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

        for name in self.classes:
            build_one(name)

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

    def resolve_devirtualized(self, owner: str, name: str, desc: str) -> str | None:
        """If exactly one concrete method implementation exists in `owner`'s
        closed-world subtype cone, return its defining class; else None."""
        key = (owner, name, desc)
        if key in self.devirt_cache:
            return self.devirt_cache[key]

        if owner not in self.classes:
            self.devirt_cache[key] = None
            return None

        concrete_impls: set[str] = set()
        for sub in self._subtypes(owner):
            ci = self.classes[sub]
            if ci.cf.is_interface or (ci.cf.access & ACC_ABSTRACT):
                continue
            # Walk up from sub to find nearest concrete implementation
            cur: str | None = sub
            while cur and cur in self.classes:
                cci = self.classes[cur]
                if any(
                    not m.is_static()
                    and not (m.access & ACC_ABSTRACT)
                    and m.name == name
                    and m.descriptor == desc
                    for m in cci.cf.methods
                ):
                    concrete_impls.add(cur)
                    break
                cur = cci.cf.super_name

        res = next(iter(concrete_impls)) if len(concrete_impls) == 1 else None
        self.devirt_cache[key] = res
        return res

    def devirtualization_stats(self) -> tuple[int, int]:
        """Count (total virtual/interface methods in known classes, devirtualizable)."""
        total = devirt = 0
        for name, ci in self.classes.items():
            for m in ci.cf.methods:
                if m.is_static() or m.name in ("<init>", "<clinit>"):
                    continue
                total += 1
                if self.resolve_devirtualized(name, m.name, m.descriptor) is not None:
                    devirt += 1
        return total, devirt
