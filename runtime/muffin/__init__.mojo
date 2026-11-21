"""Muffin zero-GC, multicore Mojo 1.2 JVM runtime."""

from .core import (
    DropFn,
    JArrayData,
    JRef,
    JStringData,
    JThrowable,
    ObjHeader,
    RawFn,
    TypeInfo,
    alloc_raw,
    array_get,
    array_len,
    array_set,
    check_cast,
    free_raw,
    is_instance,
    new_array,
    new_string,
    string_value,
)
from .pool import RangeTaskFn, cpu_count, parallel_for_range
