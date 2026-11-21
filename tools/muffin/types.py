"""
JVM type descriptors, identifier mangling, and Mojo 1.2 type mapping.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

PRIMITIVES = {
    "Z": "Bool",
    "B": "Int8",
    "C": "UInt16",
    "S": "Int16",
    "I": "Int32",
    "J": "Int64",
    "F": "Float32",
    "D": "Float64",
    "V": "None",
}

MOJO_KEYWORDS = {
    "alias", "and", "as", "async", "await", "break", "case", "class",
    "comptime", "continue", "def", "deinit", "del", "elif", "else", "except",
    "False", "finally", "fn", "for", "from", "global", "if", "import", "in",
    "is", "lambda", "match", "mut", "None", "nonlocal", "not", "or", "out",
    "owned", "pass", "raise", "raises", "read", "ref", "return", "Self",
    "self", "struct", "trait", "True", "try", "var", "where", "while", "with",
    "yield",
}


@dataclass(frozen=True, slots=True)
class JType:
    """Parsed JVM field/return/parameter type."""

    desc: str  # e.g. "I", "Ljava/lang/String;", "[I"

    @property
    def is_primitive(self) -> bool:
        return self.desc in PRIMITIVES

    @property
    def is_void(self) -> bool:
        return self.desc == "V"

    @property
    def is_wide(self) -> bool:
        return self.desc in ("J", "D")

    @property
    def is_ref(self) -> bool:
        return self.desc.startswith(("L", "["))

    @property
    def array_dims(self) -> int:
        n = 0
        while n < len(self.desc) and self.desc[n] == "[":
            n += 1
        return n

    @property
    def class_name(self) -> str | None:
        if self.desc.startswith("L") and self.desc.endswith(";"):
            return self.desc[1:-1]
        return None

    def mojo_type(self) -> str:
        if self.desc in PRIMITIVES:
            return PRIMITIVES[self.desc]
        return "JRef"

    def mojo_default(self) -> str:
        return {
            "Z": "False",
            "B": "Int8(0)",
            "C": "UInt16(0)",
            "S": "Int16(0)",
            "I": "Int32(0)",
            "J": "Int64(0)",
            "F": "Float32(0.0)",
            "D": "Float64(0.0)",
        }.get(self.desc, "JRef()")


def parse_type(desc: str, pos: int = 0) -> tuple[JType, int]:
    ch = desc[pos]
    if ch in PRIMITIVES:
        return JType(ch), pos + 1
    if ch == "L":
        end = desc.index(";", pos)
        return JType(desc[pos : end + 1]), end + 1
    if ch == "[":
        i = pos
        while desc[i] == "[":
            i += 1
        elem, end = parse_type(desc, i)
        return JType(desc[pos:end]), end
    raise ValueError(f"invalid JVM type descriptor {desc!r} at {pos}")


def parse_method_desc(desc: str) -> tuple[list[JType], JType]:
    assert desc[0] == "(", f"bad method descriptor {desc!r}"
    params: list[JType] = []
    i = 1
    while desc[i] != ")":
        t, i = parse_type(desc, i)
        params.append(t)
    ret, _ = parse_type(desc, i + 1)
    return params, ret


def safe_ident(name: str) -> str:
    cleaned = "".join(c if (c.isalnum() or c == "_") else "_" for c in name)
    if not cleaned or cleaned[0].isdigit() or cleaned in MOJO_KEYWORDS:
        cleaned = f"j_{cleaned}"
    return cleaned


def mangle_class(internal_name: str) -> str:
    return "C_" + safe_ident(internal_name.replace("/", "_").replace("$", "_D_"))


def mangle_method(owner: str, name: str, desc: str) -> str:
    if name == "<init>":
        base = "ctor"
    elif name == "<clinit>":
        base = "clinit"
    else:
        base = safe_ident(name)
    sig_hash = hashlib.blake2b(desc.encode(), digest_size=4).hexdigest()
    return f"m_{mangle_class(owner)[2:]}_{base}_{sig_hash}"
