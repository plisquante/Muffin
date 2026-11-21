"""
JVM class-file parser (JVMS SE 25, chapter 4).

Pure-Python, zero dependencies. Produces a faithful, lossless model of a
.class file that the later pipeline stages (analysis, IR lifting, Mojo codegen)
consume. Nothing here is Minecraft-specific.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Any

# --------------------------------------------------------------------------- #
# Access flags
# --------------------------------------------------------------------------- #
ACC_PUBLIC = 0x0001
ACC_PRIVATE = 0x0002
ACC_PROTECTED = 0x0004
ACC_STATIC = 0x0008
ACC_FINAL = 0x0010
ACC_SYNCHRONIZED = 0x0020  # methods
ACC_SUPER = 0x0020  # classes
ACC_VOLATILE = 0x0040  # fields
ACC_BRIDGE = 0x0040  # methods
ACC_TRANSIENT = 0x0080  # fields
ACC_VARARGS = 0x0080  # methods
ACC_NATIVE = 0x0100
ACC_INTERFACE = 0x0200
ACC_ABSTRACT = 0x0400
ACC_STRICT = 0x0800
ACC_SYNTHETIC = 0x1000
ACC_ANNOTATION = 0x2000
ACC_ENUM = 0x4000
ACC_MODULE = 0x8000

# --------------------------------------------------------------------------- #
# Constant pool
# --------------------------------------------------------------------------- #
CONSTANT_Utf8 = 1
CONSTANT_Integer = 3
CONSTANT_Float = 4
CONSTANT_Long = 5
CONSTANT_Double = 6
CONSTANT_Class = 7
CONSTANT_String = 8
CONSTANT_Fieldref = 9
CONSTANT_Methodref = 10
CONSTANT_InterfaceMethodref = 11
CONSTANT_NameAndType = 12
CONSTANT_MethodHandle = 15
CONSTANT_MethodType = 16
CONSTANT_Dynamic = 17
CONSTANT_InvokeDynamic = 18
CONSTANT_Module = 19
CONSTANT_Package = 20


class ClassFormatError(Exception):
    pass


def _decode_mutf8(b: bytes) -> str:
    """Decode JVM 'modified UTF-8' (0xC0 0x80 for NUL, CESU-8 surrogates)."""
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        pass
    out: list[str] = []
    i, n = 0, len(b)
    while i < n:
        c = b[i]
        if c < 0x80:
            out.append(chr(c))
            i += 1
        elif c & 0xE0 == 0xC0:
            out.append(chr(((c & 0x1F) << 6) | (b[i + 1] & 0x3F)))
            i += 2
        else:
            out.append(chr(((c & 0x0F) << 12) | ((b[i + 1] & 0x3F) << 6) | (b[i + 2] & 0x3F)))
            i += 3
    # Re-join surrogate pairs produced by CESU-8 (allow unpaired surrogates).
    return "".join(out).encode("utf-16", "surrogatepass").decode("utf-16", "surrogatepass")


@dataclass(slots=True)
class CPEntry:
    tag: int
    a: Any = None  # primary payload (value / index)
    b: Any = None  # secondary index


class ConstantPool:
    def __init__(self, entries: list[CPEntry | None]):
        self.entries = entries

    def __len__(self) -> int:
        return len(self.entries)

    def __getitem__(self, i: int) -> CPEntry:
        e = self.entries[i]
        if e is None:
            raise ClassFormatError(f"invalid constant pool index {i}")
        return e

    def utf8(self, i: int) -> str:
        return self[i].a

    def class_name(self, i: int) -> str:
        return self.utf8(self[i].a)

    def name_and_type(self, i: int) -> tuple[str, str]:
        e = self[i]
        return self.utf8(e.a), self.utf8(e.b)

    def member_ref(self, i: int) -> tuple[str, str, str]:
        """Field/Method/InterfaceMethod ref -> (owner, name, descriptor)."""
        e = self[i]
        name, desc = self.name_and_type(e.b)
        return self.class_name(e.a), name, desc

    def method_handle(self, i: int) -> tuple[int, tuple[str, str, str]]:
        e = self[i]
        return e.a, self.member_ref(e.b)

    def loadable(self, i: int) -> Any:
        """Resolve an ldc/bootstrap-arg constant to a Python value / tagged tuple."""
        e = self[i]
        t = e.tag
        if t in (CONSTANT_Integer, CONSTANT_Float, CONSTANT_Long, CONSTANT_Double):
            return e.a
        if t == CONSTANT_String:
            return self.utf8(e.a)
        if t == CONSTANT_Class:
            return ("class", self.utf8(e.a))
        if t == CONSTANT_MethodType:
            return ("methodtype", self.utf8(e.a))
        if t == CONSTANT_MethodHandle:
            return ("methodhandle",) + self.method_handle(i)
        if t == CONSTANT_Dynamic:
            name, desc = self.name_and_type(e.b)
            return ("condy", e.a, name, desc)
        raise ClassFormatError(f"tag {t} at {i} is not loadable")


# --------------------------------------------------------------------------- #
# Model
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class ExceptionHandler:
    start_pc: int
    end_pc: int
    handler_pc: int
    catch_type: str | None  # None == finally / catch-all


@dataclass(slots=True)
class Code:
    max_stack: int
    max_locals: int
    code: bytes
    handlers: list[ExceptionHandler]
    attributes: dict[str, bytes]
    insns_override: list[Any] | None = None


@dataclass(slots=True)
class Member:
    access: int
    name: str
    descriptor: str
    signature: str | None = None
    code: Code | None = None  # methods only
    constant_value: Any = None  # fields only
    exceptions: list[str] = field(default_factory=list)
    attributes: dict[str, bytes] = field(default_factory=dict)

    def is_static(self) -> bool:
        return bool(self.access & ACC_STATIC)


@dataclass(slots=True)
class BootstrapMethod:
    handle: tuple[int, tuple[str, str, str]]
    args: list[Any]


@dataclass(slots=True)
class ClassFile:
    major: int
    minor: int
    access: int
    name: str
    super_name: str | None
    interfaces: list[str]
    fields: list[Member]
    methods: list[Member]
    cp: ConstantPool
    signature: str | None = None
    source_file: str | None = None
    bootstrap_methods: list[BootstrapMethod] = field(default_factory=list)
    inner_classes: list[tuple[str, str | None, str | None, int]] = field(default_factory=list)
    nest_host: str | None = None
    nest_members: list[str] = field(default_factory=list)
    permitted_subclasses: list[str] = field(default_factory=list)
    record_components: list[tuple[str, str]] = field(default_factory=list)
    attributes: dict[str, bytes] = field(default_factory=dict)

    @property
    def is_interface(self) -> bool:
        return bool(self.access & ACC_INTERFACE)

    @property
    def is_enum(self) -> bool:
        return bool(self.access & ACC_ENUM)

    @property
    def is_record(self) -> bool:
        return self.super_name == "java/lang/Record"


# --------------------------------------------------------------------------- #
# Reader
# --------------------------------------------------------------------------- #
class _Reader:
    __slots__ = ("buf", "pos")

    def __init__(self, buf: bytes, pos: int = 0):
        self.buf = buf
        self.pos = pos

    def u1(self) -> int:
        v = self.buf[self.pos]
        self.pos += 1
        return v

    def u2(self) -> int:
        v = (self.buf[self.pos] << 8) | self.buf[self.pos + 1]
        self.pos += 2
        return v

    def u4(self) -> int:
        (v,) = struct.unpack_from(">I", self.buf, self.pos)
        self.pos += 4
        return v

    def bytes(self, n: int) -> bytes:
        v = self.buf[self.pos : self.pos + n]
        self.pos += n
        return v


def _read_cp(r: _Reader) -> ConstantPool:
    count = r.u2()
    entries: list[CPEntry | None] = [None] * count
    i = 1
    while i < count:
        tag = r.u1()
        if tag == CONSTANT_Utf8:
            n = r.u2()
            entries[i] = CPEntry(tag, _decode_mutf8(r.bytes(n)))
        elif tag == CONSTANT_Integer:
            entries[i] = CPEntry(tag, struct.unpack(">i", r.bytes(4))[0])
        elif tag == CONSTANT_Float:
            entries[i] = CPEntry(tag, struct.unpack(">f", r.bytes(4))[0])
        elif tag == CONSTANT_Long:
            entries[i] = CPEntry(tag, struct.unpack(">q", r.bytes(8))[0])
            i += 1  # 8-byte constants take two slots
        elif tag == CONSTANT_Double:
            entries[i] = CPEntry(tag, struct.unpack(">d", r.bytes(8))[0])
            i += 1
        elif tag in (CONSTANT_Class, CONSTANT_String, CONSTANT_MethodType, CONSTANT_Module, CONSTANT_Package):
            entries[i] = CPEntry(tag, r.u2())
        elif tag in (
            CONSTANT_Fieldref,
            CONSTANT_Methodref,
            CONSTANT_InterfaceMethodref,
            CONSTANT_NameAndType,
            CONSTANT_Dynamic,
            CONSTANT_InvokeDynamic,
        ):
            entries[i] = CPEntry(tag, r.u2(), r.u2())
        elif tag == CONSTANT_MethodHandle:
            entries[i] = CPEntry(tag, r.u1(), r.u2())
        else:
            raise ClassFormatError(f"unknown constant pool tag {tag} at index {i}")
        i += 1
    return ConstantPool(entries)


def _read_attrs(r: _Reader, cp: ConstantPool) -> dict[str, bytes]:
    attrs: dict[str, bytes] = {}
    for _ in range(r.u2()):
        name = cp.utf8(r.u2())
        attrs[name] = r.bytes(r.u4())
    return attrs


def _parse_code(data: bytes, cp: ConstantPool) -> Code:
    r = _Reader(data)
    max_stack, max_locals = r.u2(), r.u2()
    code = r.bytes(r.u4())
    handlers = []
    for _ in range(r.u2()):
        s, e, h, t = r.u2(), r.u2(), r.u2(), r.u2()
        handlers.append(ExceptionHandler(s, e, h, cp.class_name(t) if t else None))
    attrs = _read_attrs(r, cp)
    return Code(max_stack, max_locals, code, handlers, attrs)


def _read_member(r: _Reader, cp: ConstantPool, is_method: bool) -> Member:
    access = r.u2()
    name = cp.utf8(r.u2())
    desc = cp.utf8(r.u2())
    attrs = _read_attrs(r, cp)
    m = Member(access, name, desc, attributes=attrs)
    if "Signature" in attrs:
        m.signature = cp.utf8(_Reader(attrs["Signature"]).u2())
    if is_method:
        if "Code" in attrs:
            m.code = _parse_code(attrs["Code"], cp)
        if "Exceptions" in attrs:
            er = _Reader(attrs["Exceptions"])
            m.exceptions = [cp.class_name(er.u2()) for _ in range(er.u2())]
    elif "ConstantValue" in attrs:
        m.constant_value = cp.loadable(_Reader(attrs["ConstantValue"]).u2())
    return m


def parse_class(data: bytes) -> ClassFile:
    r = _Reader(data)
    if r.u4() != 0xCAFEBABE:
        raise ClassFormatError("bad magic")
    minor, major = r.u2(), r.u2()
    cp = _read_cp(r)
    access = r.u2()
    name = cp.class_name(r.u2())
    sup = r.u2()
    super_name = cp.class_name(sup) if sup else None
    interfaces = [cp.class_name(r.u2()) for _ in range(r.u2())]
    fields = [_read_member(r, cp, False) for _ in range(r.u2())]
    methods = [_read_member(r, cp, True) for _ in range(r.u2())]
    attrs = _read_attrs(r, cp)

    cf = ClassFile(major, minor, access, name, super_name, interfaces, fields, methods, cp, attributes=attrs)

    if "Signature" in attrs:
        cf.signature = cp.utf8(_Reader(attrs["Signature"]).u2())
    if "SourceFile" in attrs:
        cf.source_file = cp.utf8(_Reader(attrs["SourceFile"]).u2())
    if "BootstrapMethods" in attrs:
        br = _Reader(attrs["BootstrapMethods"])
        for _ in range(br.u2()):
            mh = cp.method_handle(br.u2())
            args = [cp.loadable(br.u2()) for _ in range(br.u2())]
            cf.bootstrap_methods.append(BootstrapMethod(mh, args))
    if "InnerClasses" in attrs:
        ir = _Reader(attrs["InnerClasses"])
        for _ in range(ir.u2()):
            inner, outer, iname, flags = ir.u2(), ir.u2(), ir.u2(), ir.u2()
            cf.inner_classes.append(
                (
                    cp.class_name(inner),
                    cp.class_name(outer) if outer else None,
                    cp.utf8(iname) if iname else None,
                    flags,
                )
            )
    if "NestHost" in attrs:
        cf.nest_host = cp.class_name(_Reader(attrs["NestHost"]).u2())
    if "NestMembers" in attrs:
        nr = _Reader(attrs["NestMembers"])
        cf.nest_members = [cp.class_name(nr.u2()) for _ in range(nr.u2())]
    if "PermittedSubclasses" in attrs:
        pr = _Reader(attrs["PermittedSubclasses"])
        cf.permitted_subclasses = [cp.class_name(pr.u2()) for _ in range(pr.u2())]
    if "Record" in attrs:
        rr = _Reader(attrs["Record"])
        for _ in range(rr.u2()):
            n, d = cp.utf8(rr.u2()), cp.utf8(rr.u2())
            _read_attrs(rr, cp)
            cf.record_components.append((n, d))
    return cf
