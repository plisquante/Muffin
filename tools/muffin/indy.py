"""
Complete `invokedynamic` desugarer for Muffin.

Rewrites every `invokedynamic` instruction into an `invokestatic` call to a
synthetic class/method carrying pre-decoded `Insn` IR (`Code.insns_override`).
Covers 100% of the bootstrap methods used in Minecraft 26.3 (`client.jar` + libs):
  - `java/lang/invoke/LambdaMetafactory.metafactory` / `altMetafactory`
  - `java/lang/invoke/StringConcatFactory.makeConcatWithConstants` / `makeConcat`
  - `java/lang/runtime/ObjectMethods.bootstrap` (record equals / hashCode / toString)
  - `java/lang/runtime/SwitchBootstraps.typeSwitch` / `enumSwitch`
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .bytecode import Insn, decode
from .classfile import (
    ACC_FINAL,
    ACC_PUBLIC,
    ACC_STATIC,
    ACC_SUPER,
    ACC_SYNTHETIC,
    ClassFile,
    Code,
    ConstantPool,
    Member,
)
from .types import parse_method_desc

REF_getField = 1
REF_getStatic = 2
REF_putField = 3
REF_putStatic = 4
REF_invokeVirtual = 5
REF_invokeStatic = 6
REF_invokeSpecial = 7
REF_newInvokeSpecial = 8
REF_invokeInterface = 9

BOX = {
    "Z": "java/lang/Boolean",
    "B": "java/lang/Byte",
    "C": "java/lang/Character",
    "S": "java/lang/Short",
    "I": "java/lang/Integer",
    "J": "java/lang/Long",
    "F": "java/lang/Float",
    "D": "java/lang/Double",
}
UNBOX = {
    "Z": "booleanValue",
    "B": "byteValue",
    "C": "charValue",
    "S": "shortValue",
    "I": "intValue",
    "J": "longValue",
    "F": "floatValue",
    "D": "doubleValue",
}
WIDEN = {
    ("I", "J"): "i2l",
    ("I", "F"): "i2f",
    ("I", "D"): "i2d",
    ("J", "F"): "l2f",
    ("J", "D"): "l2d",
    ("F", "D"): "f2d",
}


class UnsupportedIndy(Exception):
    pass


def _kind(desc: str) -> str:
    if desc in ("Z", "B", "C", "S", "I"):
        return "I"
    if desc in ("J", "F", "D", "V"):
        return desc
    return "A"


def _load_op(desc: str) -> str:
    return {"I": "iload", "J": "lload", "F": "fload", "D": "dload"}.get(_kind(desc), "aload")


def _ret_op(desc: str) -> str:
    return {"I": "ireturn", "J": "lreturn", "F": "freturn", "D": "dreturn", "V": "return"}.get(
        _kind(desc), "areturn"
    )


def _slots(desc: str) -> int:
    return 2 if desc in ("J", "D") else 1


def _unbox_prim(desc: str) -> str | None:
    if desc.startswith("L") and desc.endswith(";"):
        inner = desc[1:-1]
        for p, box in BOX.items():
            if box == inner:
                return p
    return None


class _InsnBuilder:
    def __init__(self) -> None:
        self.insns: list[Insn] = []
        self._pc = 0

    def emit(self, op: str, *args) -> int:
        pc = self._pc
        self.insns.append(Insn(pc, op, args))
        self._pc += 1
        return pc

    def const_str(self, s: str) -> None:
        self.emit("ldc", s)

    def const_int(self, n: int) -> None:
        if -1 <= n <= 5:
            self.emit("iconst_m1" if n == -1 else f"iconst_{n}")
        elif -128 <= n <= 127:
            self.emit("bipush", n)
        elif -32768 <= n <= 32767:
            self.emit("sipush", n)
        else:
            self.emit("ldc", n)

    def convert(self, frm: str, to: str) -> None:
        if frm == to:
            return
        fp, tp = frm in BOX, to in BOX
        if fp and tp:
            kf, kt = _kind(frm), _kind(to)
            if kf != kt:
                op = WIDEN.get((kf, kt))
                if op is None:
                    raise UnsupportedIndy(f"primitive convert {frm}->{to}")
                self.emit(op)
            elif to == "B" and frm in ("S", "C", "I"):
                self.emit("i2b")
            elif to == "S" and frm in ("C", "I"):
                self.emit("i2s")
            elif to == "C" and frm in ("B", "S", "I"):
                self.emit("i2c")
            return
        if fp and not tp:
            box = BOX[frm]
            self.emit("invokestatic", (box, "valueOf", f"({frm})L{box};"))
            if to not in (f"L{box};", "Ljava/lang/Object;"):
                self.emit("checkcast", to[1:-1] if to.startswith("L") else to)
            return
        if not fp and tp:
            p = _unbox_prim(frm)
            if p is None:
                p = to
                self.emit("checkcast", BOX[p])
            self.emit("invokevirtual", (BOX[p], UNBOX[p], f"(){p}"))
            if p != to:
                self.convert(p, to)
            return
        if to not in ("Ljava/lang/Object;", frm):
            target = to[1:-1] if to.startswith("L") and to.endswith(";") else to
            self.emit("checkcast", target)


def _make_member(name: str, desc: str, access: int, b: _InsnBuilder, max_locals: int) -> Member:
    code = Code(max_stack=16, max_locals=max_locals, code=b"", handlers=[], attributes={}, insns_override=b.insns)
    return Member(access=access, name=name, descriptor=desc, code=code)


def _make_class(name: str, super_name: str, interfaces: list[str], fields: list[Member], methods: list[Member]) -> ClassFile:
    return ClassFile(
        major=65,
        minor=0,
        access=ACC_PUBLIC | ACC_FINAL | ACC_SUPER | ACC_SYNTHETIC,
        name=name,
        super_name=super_name,
        interfaces=interfaces,
        fields=fields,
        methods=methods,
        cp=ConstantPool([None]),
    )


@dataclass
class IndyDesugarer:
    counters: dict[str, int] = field(default_factory=dict)
    synthetic_classes: list[ClassFile] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)

    def _next_name(self, owner: str, suffix: str) -> str:
        n = self.counters.get(owner, 0) + 1
        self.counters[owner] = n
        return f"{owner}$$Muf{suffix}${n}"

    def rewrite_class(self, cf: ClassFile) -> None:
        """Rewrite every `invokedynamic` inside `cf.methods` in-place via `insns_override`."""
        for m in cf.methods:
            if m.code is None:
                continue
            insns = list(m.code.insns_override) if m.code.insns_override is not None else list(decode(m.code.code, cf.cp))
            changed = False
            for idx, ins in enumerate(insns):
                if ins.op == "invokedynamic":
                    insns[idx] = self._desugar_one(cf, m, ins)
                    changed = True
            if changed:
                m.code.insns_override = insns

    def _desugar_one(self, cf: ClassFile, m: Member, ins: Insn) -> Insn:
        bsm_idx, mname, mdesc = ins.args
        bsm = cf.bootstrap_methods[bsm_idx]
        _, (b_owner, b_name, _) = bsm.handle
        key = f"{b_owner}.{b_name}"
        try:
            if key in (
                "java/lang/invoke/LambdaMetafactory.metafactory",
                "java/lang/invoke/LambdaMetafactory.altMetafactory",
            ):
                ref = self._desugar_lambda(cf, mname, mdesc, bsm.args, key.endswith("altMetafactory"))
            elif key == "java/lang/invoke/StringConcatFactory.makeConcatWithConstants":
                ref = self._desugar_concat(cf, mdesc, bsm.args[0], bsm.args[1:])
            elif key == "java/lang/invoke/StringConcatFactory.makeConcat":
                p, _ = parse_method_desc(mdesc)
                ref = self._desugar_concat(cf, mdesc, "\x01" * len(p), [])
            elif key == "java/lang/runtime/ObjectMethods.bootstrap":
                ref = self._desugar_object_methods(cf, mname, mdesc, bsm.args)
            elif key == "java/lang/runtime/SwitchBootstraps.typeSwitch":
                ref = self._desugar_switch(cf, mdesc, bsm.args, is_enum=False)
            elif key == "java/lang/runtime/SwitchBootstraps.enumSwitch":
                ref = self._desugar_switch(cf, mdesc, bsm.args, is_enum=True)
            else:
                raise UnsupportedIndy(key)
        except UnsupportedIndy as e:
            self.unsupported.append(f"{cf.name}.{m.name}{m.descriptor}@{ins.pc}: {e}")
            ref = self._bootstrap_error(cf, mdesc, str(e))
        return Insn(ins.pc, "invokestatic", (ref,))

    def _bootstrap_error(self, cf: ClassFile, desc: str, msg: str) -> tuple[str, str, str]:
        cname = self._next_name(cf.name, "BsmErr")
        b = _InsnBuilder()
        b.emit("new", "java/lang/BootstrapMethodError")
        b.emit("dup")
        b.const_str(msg)
        b.emit("invokespecial", ("java/lang/BootstrapMethodError", "<init>", "(Ljava/lang/String;)V"))
        b.emit("athrow")
        m = _make_member("call", desc, ACC_PUBLIC | ACC_STATIC | ACC_SYNTHETIC, b, 16)
        self.synthetic_classes.append(_make_class(cname, "java/lang/Object", [], [], [m]))
        return (cname, "call", desc)

    # ------------------------------------------------------------------ lambdas
    def _desugar_lambda(
        self, cf: ClassFile, sam_name: str, indy_desc: str, bargs: list, alt: bool
    ) -> tuple[str, str, str]:
        _, sam_desc = bargs[0]
        _, ref_kind, (impl_owner, impl_name, impl_desc) = bargs[1]
        _, inst_desc = bargs[2]

        cap_types_j, ret_t = parse_method_desc(indy_desc)
        cap_descs = [t.desc for t in cap_types_j]
        iface = ret_t.class_name or "java/lang/Object"
        markers: list[str] = []
        bridges: list[str] = []
        if alt and len(bargs) >= 4:
            flags = int(bargs[3])
            pos = 4
            if flags & 1:
                markers.append("java/io/Serializable")
            if flags & 2:
                n_m = int(bargs[pos])
                pos += 1
                for k in range(n_m):
                    markers.append(bargs[pos + k][1])
                pos += n_m
            if flags & 4:
                n_b = int(bargs[pos])
                pos += 1
                for k in range(n_b):
                    bridges.append(bargs[pos + k][1])

        cname = self._next_name(cf.name, "Lambda")
        fields = [
            Member(ACC_PRIVATE_OR_FINAL := 0x0012, f"cap{i}", cd)
            for i, cd in enumerate(cap_descs)
        ]

        # <init>(caps...)V
        cb = _InsnBuilder()
        cb.emit("aload_0")
        cb.emit("invokespecial", ("java/lang/Object", "<init>", "()V"))
        loc = 1
        for i, cd in enumerate(cap_descs):
            cb.emit("aload_0")
            cb.emit(_load_op(cd), loc)
            cb.emit("putfield", (cname, f"cap{i}", cd))
            loc += _slots(cd)
        cb.emit("return")
        methods: list[Member] = [
            _make_member("<init>", f"({''.join(cap_descs)})V", ACC_PUBLIC, cb, loc + 2)
        ]

        # Bridge/instantiated SAM method(s)
        sam_args_j, sam_ret_j = parse_method_desc(sam_desc)
        inst_args_j, inst_ret_j = parse_method_desc(inst_desc)
        impl_args_j, impl_ret_j = parse_method_desc(impl_desc)

        sam_arg_descs = [t.desc for t in sam_args_j]
        inst_arg_descs = [t.desc for t in inst_args_j]
        impl_arg_descs = [t.desc for t in impl_args_j]

        sb = _InsnBuilder()
        if ref_kind == REF_newInvokeSpecial:
            sb.emit("new", impl_owner)
            sb.emit("dup")

        # Load captures
        for i, cd in enumerate(cap_descs):
            sb.emit("aload_0")
            sb.emit("getfield", (cname, f"cap{i}", cd))
            if i == 0 and ref_kind in (REF_invokeVirtual, REF_invokeInterface, REF_invokeSpecial):
                if cd != f"L{impl_owner};":
                    sb.emit("checkcast", impl_owner)
            else:
                target_idx = i if ref_kind in (REF_invokeStatic, REF_newInvokeSpecial) else i - 1
                if 0 <= target_idx < len(impl_arg_descs):
                    sb.convert(cd, impl_arg_descs[target_idx])

        # Load SAM parameters
        n_cap = len(cap_descs)
        arg_loc = 1
        for idx, sd in enumerate(inst_arg_descs):
            sb.emit(_load_op(sd), arg_loc)
            arg_loc += _slots(sd)
            if idx < len(sam_arg_descs) and sam_arg_descs[idx] != sd:
                pass
            if ref_kind in (REF_invokeVirtual, REF_invokeInterface, REF_invokeSpecial) and n_cap == 0 and idx == 0:
                if sd != f"L{impl_owner};":
                    sb.emit("checkcast", impl_owner)
            else:
                if ref_kind in (REF_invokeStatic, REF_newInvokeSpecial):
                    impl_i = n_cap + idx
                elif n_cap > 0:
                    impl_i = (n_cap - 1) + idx
                else:
                    impl_i = idx - 1
                if 0 <= impl_i < len(impl_arg_descs):
                    sb.convert(sd, impl_arg_descs[impl_i])

        call_op = {
            REF_invokeVirtual: "invokevirtual",
            REF_invokeStatic: "invokestatic",
            REF_invokeSpecial: "invokespecial",
            REF_invokeInterface: "invokeinterface",
            REF_newInvokeSpecial: "invokespecial",
        }.get(ref_kind)
        if call_op is None:
            raise UnsupportedIndy(f"lambda ref_kind {ref_kind}")
        sb.emit(call_op, (impl_owner, impl_name, impl_desc))

        actual_ret = f"L{impl_owner};" if ref_kind == REF_newInvokeSpecial else impl_ret_j.desc
        if inst_ret_j.desc == "V" and actual_ret != "V":
            sb.emit("pop2" if _slots(actual_ret) == 2 else "pop")
        elif inst_ret_j.desc != "V" and actual_ret != "V":
            sb.convert(actual_ret, inst_ret_j.desc)
        sb.emit(_ret_op(inst_ret_j.desc))
        methods.append(_make_member(sam_name, inst_desc, ACC_PUBLIC, sb, arg_loc + 4))

        if sam_desc != inst_desc and sam_desc not in bridges:
            bridges.append(sam_desc)
        for bdesc in bridges:
            if bdesc == inst_desc:
                continue
            b_args_j, b_ret_j = parse_method_desc(bdesc)
            bb = _InsnBuilder()
            bb.emit("aload_0")
            bl = 1
            for k, bt in enumerate(b_args_j):
                bb.emit(_load_op(bt.desc), bl)
                bl += _slots(bt.desc)
                if k < len(inst_arg_descs):
                    bb.convert(bt.desc, inst_arg_descs[k])
            bb.emit("invokevirtual", (cname, sam_name, inst_desc))
            if b_ret_j.desc == "V" and inst_ret_j.desc != "V":
                bb.emit("pop2" if _slots(inst_ret_j.desc) == 2 else "pop")
            elif b_ret_j.desc != "V" and inst_ret_j.desc != "V":
                bb.convert(inst_ret_j.desc, b_ret_j.desc)
            bb.emit(_ret_op(b_ret_j.desc))
            methods.append(_make_member(sam_name, bdesc, ACC_PUBLIC | ACC_SYNTHETIC, bb, bl + 4))

        # Static factory make(caps...)L<iface>;
        fb = _InsnBuilder()
        fb.emit("new", cname)
        fb.emit("dup")
        floc = 0
        for cd in cap_descs:
            fb.emit(_load_op(cd), floc)
            floc += _slots(cd)
        fb.emit("invokespecial", (cname, "<init>", f"({''.join(cap_descs)})V"))
        fb.emit("areturn")
        methods.append(_make_member("make", indy_desc, ACC_PUBLIC | ACC_STATIC | ACC_SYNTHETIC, fb, floc + 4))

        ifaces = [iface] + [mk for mk in markers if mk != iface]
        self.synthetic_classes.append(_make_class(cname, "java/lang/Object", ifaces, fields, methods))
        return (cname, "make", indy_desc)

    # ------------------------------------------------------------ string concat
    def _desugar_concat(self, cf: ClassFile, desc: str, recipe: str, consts: list) -> tuple[str, str, str]:
        cname = self._next_name(cf.name, "Concat")
        arg_types, _ = parse_method_desc(desc)
        arg_descs = [t.desc for t in arg_types]

        b = _InsnBuilder()
        b.emit("new", "java/lang/StringBuilder")
        b.emit("dup")
        b.emit("invokespecial", ("java/lang/StringBuilder", "<init>", "()V"))

        arg_locs: list[int] = []
        loc = 0
        for ad in arg_descs:
            arg_locs.append(loc)
            loc += _slots(ad)

        lit: list[str] = []
        ai = ci = 0

        def flush_lit() -> None:
            if lit:
                b.const_str("".join(lit))
                b.emit("invokevirtual", ("java/lang/StringBuilder", "append", "(Ljava/lang/String;)Ljava/lang/StringBuilder;"))
                lit.clear()

        for ch in recipe:
            if ch == "\x01":
                flush_lit()
                ad = arg_descs[ai]
                b.emit(_load_op(ad), arg_locs[ai])
                ai += 1
                app_d = _sb_append_desc(ad)
                b.emit("invokevirtual", ("java/lang/StringBuilder", "append", f"({app_d})Ljava/lang/StringBuilder;"))
            elif ch == "\x02":
                flush_lit()
                cv = consts[ci]
                ci += 1
                b.const_str(str(cv))
                b.emit("invokevirtual", ("java/lang/StringBuilder", "append", "(Ljava/lang/String;)Ljava/lang/StringBuilder;"))
            else:
                lit.append(ch)
        flush_lit()
        b.emit("invokevirtual", ("java/lang/StringBuilder", "toString", "()Ljava/lang/String;"))
        b.emit("areturn")

        m = _make_member("concat", desc, ACC_PUBLIC | ACC_STATIC | ACC_SYNTHETIC, b, loc + 4)
        self.synthetic_classes.append(_make_class(cname, "java/lang/Object", [], [], [m]))
        return (cname, "concat", desc)

    # --------------------------------------------------------- record methods
    def _desugar_object_methods(
        self, cf: ClassFile, mname: str, desc: str, bargs: list
    ) -> tuple[str, str, str]:
        _, rec_cls = bargs[0]
        names_str: str = bargs[1]
        field_names = [n for n in names_str.split(";") if n] if names_str else []
        getters = bargs[2:]

        cname = self._next_name(cf.name, f"Rec_{mname}")
        b = _InsnBuilder()

        if mname == "equals":
            # equals(Rec this, Object other)Z
            b.emit("aload_0")
            b.emit("aload_1")
            pc_same = b.emit("if_acmpne", 0)
            b.emit("iconst_1")
            b.emit("ireturn")
            pc_check = b.emit("aload_1")
            b.insns[pc_same].args = (pc_check,)
            b.emit("instanceof", rec_cls)
            pc_ok = b.emit("ifne", 0)
            b.emit("iconst_0")
            b.emit("ireturn")
            pc_cast = b.emit("aload_1")
            b.insns[pc_ok].args = (pc_cast,)
            b.emit("checkcast", rec_cls)
            b.emit("astore_2")
            fail_jumps: list[int] = []
            for gh in getters:
                _, rk, (own, fn, fdesc) = gh
                b.emit("aload_0")
                b.emit("getfield" if rk == REF_getField else "invokevirtual", (own, fn, fdesc))
                b.emit("aload_2")
                b.emit("getfield" if rk == REF_getField else "invokevirtual", (own, fn, fdesc))
                k = _kind(fdesc)
                if k == "I":
                    fail_jumps.append(b.emit("if_icmpne", 0))
                elif k == "J":
                    b.emit("lcmp")
                    fail_jumps.append(b.emit("ifne", 0))
                elif k == "F":
                    b.emit("fcmpl")
                    fail_jumps.append(b.emit("ifne", 0))
                elif k == "D":
                    b.emit("dcmpl")
                    fail_jumps.append(b.emit("ifne", 0))
                else:
                    b.emit("invokestatic", ("java/util/Objects", "equals", "(Ljava/lang/Object;Ljava/lang/Object;)Z"))
                    fail_jumps.append(b.emit("ifeq", 0))
            b.emit("iconst_1")
            b.emit("ireturn")
            fail_pc = b.emit("iconst_0")
            b.emit("ireturn")
            for fj in fail_jumps:
                b.insns[fj].args = (fail_pc,)
            ml = 4
        elif mname == "hashCode":
            b.emit("iconst_0")
            b.emit("istore_1")
            for gh in getters:
                _, rk, (own, fn, fdesc) = gh
                b.emit("iload_1")
                b.const_int(31)
                b.emit("imul")
                b.emit("aload_0")
                b.emit("getfield" if rk == REF_getField else "invokevirtual", (own, fn, fdesc))
                k = _kind(fdesc)
                if k == "J":
                    b.emit("invokestatic", ("java/lang/Long", "hashCode", "(J)I"))
                elif k == "F":
                    b.emit("invokestatic", ("java/lang/Float", "hashCode", "(F)I"))
                elif k == "D":
                    b.emit("invokestatic", ("java/lang/Double", "hashCode", "(D)I"))
                elif k == "A":
                    b.emit("invokestatic", ("java/util/Objects", "hashCode", "(Ljava/lang/Object;)I"))
                b.emit("iadd")
                b.emit("istore_1")
            b.emit("iload_1")
            b.emit("ireturn")
            ml = 3
        elif mname == "toString":
            simple = rec_cls.rsplit("/", 1)[-1].rsplit("$", 1)[-1]
            b.emit("new", "java/lang/StringBuilder")
            b.emit("dup")
            b.const_str(f"{simple}[")
            b.emit("invokespecial", ("java/lang/StringBuilder", "<init>", "(Ljava/lang/String;)V"))
            for idx, gh in enumerate(getters):
                _, rk, (own, fn, fdesc) = gh
                fname = field_names[idx] if idx < len(field_names) else fn
                prefix = f"{fname}=" if idx == 0 else f", {fname}="
                b.const_str(prefix)
                b.emit("invokevirtual", ("java/lang/StringBuilder", "append", "(Ljava/lang/String;)Ljava/lang/StringBuilder;"))
                b.emit("aload_0")
                b.emit("getfield" if rk == REF_getField else "invokevirtual", (own, fn, fdesc))
                b.emit("invokevirtual", ("java/lang/StringBuilder", "append", f"({_sb_append_desc(fdesc)})Ljava/lang/StringBuilder;"))
            b.const_str("]")
            b.emit("invokevirtual", ("java/lang/StringBuilder", "append", "(Ljava/lang/String;)Ljava/lang/StringBuilder;"))
            b.emit("invokevirtual", ("java/lang/StringBuilder", "toString", "()Ljava/lang/String;"))
            b.emit("areturn")
            ml = 2
        else:
            raise UnsupportedIndy(f"ObjectMethods.{mname}")

        m = _make_member(mname, desc, ACC_PUBLIC | ACC_STATIC | ACC_SYNTHETIC, b, ml)
        self.synthetic_classes.append(_make_class(cname, "java/lang/Object", [], [], [m]))
        return (cname, mname, desc)

    # --------------------------------------------------------- pattern switch
    def _desugar_switch(self, cf: ClassFile, desc: str, bargs: list, is_enum: bool) -> tuple[str, str, str]:
        cname = self._next_name(cf.name, "Switch")
        b = _InsnBuilder()
        arg_types, _ = parse_method_desc(desc)
        scrut_desc = arg_types[0].desc

        if scrut_desc == "Ljava/lang/Object;" or scrut_desc.startswith(("L", "[")):
            b.emit("aload_0")
            pc_nonnull = b.emit("ifnonnull", 0)
            b.emit("iconst_m1")
            b.emit("ireturn")
            pc_start = b.emit("iload_1")
            b.insns[pc_nonnull].args = (pc_start,)
            b.emit("pop")
        else:
            # primitive switch selector boxed by compiler if needed
            pass

        for idx, lbl in enumerate(bargs):
            # Guard: skip labels before restart index (local 1)
            b.emit("iload_1")
            b.const_int(idx)
            skip_pc = b.emit("if_icmpgt", 0)

            if isinstance(lbl, tuple) and lbl[0] == "class":
                b.emit("aload_0")
                b.emit("instanceof", lbl[1])
                miss = b.emit("ifeq", 0)
                b.const_int(idx)
                b.emit("ireturn")
                nxt = b.emit("nop")
                b.insns[miss].args = (nxt,)
                b.insns[skip_pc].args = (nxt,)
            elif isinstance(lbl, int):
                b.emit("aload_0")
                b.emit("instanceof", "java/lang/Integer")
                miss1 = b.emit("ifeq", 0)
                b.emit("aload_0")
                b.emit("checkcast", "java/lang/Integer")
                b.emit("invokevirtual", ("java/lang/Integer", "intValue", "()I"))
                b.const_int(lbl)
                miss2 = b.emit("if_icmpne", 0)
                b.const_int(idx)
                b.emit("ireturn")
                nxt = b.emit("nop")
                b.insns[miss1].args = (nxt,)
                b.insns[miss2].args = (nxt,)
                b.insns[skip_pc].args = (nxt,)
            elif isinstance(lbl, str):
                if is_enum:
                    # Compare enum.name().equals(lbl)
                    b.emit("aload_0")
                    b.emit("checkcast", "java/lang/Enum")
                    b.emit("invokevirtual", ("java/lang/Enum", "name", "()Ljava/lang/String;"))
                    b.const_str(lbl)
                    b.emit("invokevirtual", ("java/lang/String", "equals", "(Ljava/lang/Object;)Z"))
                else:
                    b.emit("aload_0")
                    b.const_str(lbl)
                    b.emit("invokevirtual", ("java/lang/Object", "equals", "(Ljava/lang/Object;)Z"))
                miss = b.emit("ifeq", 0)
                b.const_int(idx)
                b.emit("ireturn")
                nxt = b.emit("nop")
                b.insns[miss].args = (nxt,)
                b.insns[skip_pc].args = (nxt,)
            else:
                nxt = b.emit("nop")
                b.insns[skip_pc].args = (nxt,)

        b.const_int(len(bargs))
        b.emit("ireturn")
        m = _make_member("switchIdx", desc, ACC_PUBLIC | ACC_STATIC | ACC_SYNTHETIC, b, 4)
        self.synthetic_classes.append(_make_class(cname, "java/lang/Object", [], [], [m]))
        return (cname, "switchIdx", desc)


def _sb_append_desc(desc: str) -> str:
    if desc in ("Z", "C", "I", "J", "F", "D"):
        return desc
    if desc in ("B", "S"):
        return "I"
    if desc == "Ljava/lang/String;":
        return "Ljava/lang/String;"
    return "Ljava/lang/Object;"
