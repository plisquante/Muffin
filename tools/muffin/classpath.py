"""
Multi-release JAR, directory, and JDK `.jmod` classpath loader for Muffin.

Supports:
- Directories of `.class` files
- `.jar` / `.zip` archives with multi-release `META-INF/versions/N` resolution
- Per-prefix release overrides (e.g. `{"lwjgl": 11}` selects LWJGL's standard
  JNI classes rather than its Java 25 `java.lang.foreign` FFM variant)
- JDK `.jmod` archives (`JM\\x01\\x00` header + zip)
- `META-INF/services/` discovery for `ServiceLoader`
"""

from __future__ import annotations

import io
import os
import zipfile
from pathlib import Path
from typing import Iterator

from .classfile import ClassFile, parse_class


class _Source:
    label: str

    def names(self) -> Iterator[str]:
        raise NotImplementedError

    def read(self, name: str) -> bytes | None:
        raise NotImplementedError

    def services(self) -> dict[str, list[str]]:
        return {}


class DirSource(_Source):
    def __init__(self, path: str | Path):
        self.path = str(path)
        self.label = self.path
        self._dir_cache: dict[str, frozenset[str] | None] = {}

    def names(self) -> Iterator[str]:
        for root, _, files in os.walk(self.path):
            for f in files:
                if f.endswith(".class") and f != "module-info.class":
                    full = os.path.join(root, f)
                    yield os.path.relpath(full, self.path)[:-6].replace(os.sep, "/")

    def read(self, name: str) -> bytes | None:
        slash = name.rfind("/")
        if slash >= 0:
            d = os.path.join(self.path, name[:slash])
            fname = name[slash + 1 :] + ".class"
        else:
            d = self.path
            fname = name + ".class"
        entries = self._dir_cache.get(d)
        if entries is None and d not in self._dir_cache:
            try:
                with os.scandir(d) as it:
                    entries = frozenset(e.name for e in it if e.name.endswith(".class"))
            except OSError:
                entries = None
            self._dir_cache[d] = entries
        if not entries or fname not in entries:
            return None
        with open(os.path.join(d, fname), "rb") as fh:
            return fh.read()


class ZipSource(_Source):
    def __init__(self, path: str | Path, prefix: str = "", release: int = 25):
        self.path = str(path)
        self.label = os.path.basename(self.path)
        if self.path.endswith(".jmod"):
            with open(self.path, "rb") as fh:
                data = fh.read()
            self.zf = zipfile.ZipFile(io.BytesIO(data[4:]))
            prefix = "classes/"
        else:
            self.zf = zipfile.ZipFile(self.path)
        self.prefix = prefix
        self.index: dict[str, str] = {}
        self._service_files: list[str] = []
        best: dict[str, int] = {}
        for e in self.zf.namelist():
            if e.startswith("META-INF/services/") and not e.endswith("/"):
                self._service_files.append(e)
            if not e.endswith(".class") or not e.startswith(prefix):
                continue
            rel = e[len(prefix) :]
            ver = 0
            if rel.startswith("META-INF/versions/"):
                parts = rel.split("/", 3)
                try:
                    ver = int(parts[2])
                except (ValueError, IndexError):
                    continue
                if ver > release:
                    continue
                rel = parts[3]
            if rel.endswith("module-info.class"):
                continue
            n = rel[:-6]
            if ver >= best.get(n, -1):
                best[n] = ver
                self.index[n] = e

    def names(self) -> Iterator[str]:
        return iter(self.index.keys())

    def read(self, name: str) -> bytes | None:
        e = self.index.get(name)
        return self.zf.read(e) if e else None

    def services(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for e in self._service_files:
            svc = e[len("META-INF/services/") :].replace(".", "/")
            if "/" not in svc and "." not in svc:
                continue
            txt = self.zf.read(e).decode("utf-8", errors="replace")
            providers: list[str] = []
            for line in txt.splitlines():
                line = line.split("#", 1)[0].strip()
                if line:
                    providers.append(line.replace(".", "/"))
            if providers:
                out.setdefault(svc, []).extend(providers)
        return out


class ClassPath:
    def __init__(
        self,
        entries: list[str | Path],
        release: int = 25,
        release_overrides: dict[str, int] | None = None,
        boot_start: int | None = None,
    ):
        self.sources: list[_Source] = []
        overrides = {"lwjgl": 11} if release_overrides is None else release_overrides
        for e in entries:
            p = str(e)
            if os.path.isdir(p):
                self.sources.append(DirSource(p))
            else:
                rel = release
                base = os.path.basename(p)
                for prefix, r in overrides.items():
                    if base.startswith(prefix):
                        rel = r
                        break
                self.sources.append(ZipSource(p, release=rel))
        self._cache: dict[str, ClassFile | None] = {}
        self.origin: dict[str, str] = {}
        self.boot_start = len(self.sources) if boot_start is None else boot_start
        self.boot: set[str] = set()

    def find(self, name: str) -> ClassFile | None:
        if name in self._cache:
            return self._cache[name]
        cf = None
        for i, s in enumerate(self.sources):
            b = s.read(name)
            if b is not None:
                cf = parse_class(b)
                self.origin[name] = s.label
                if i >= self.boot_start:
                    self.boot.add(name)
                break
        self._cache[name] = cf
        return cf

    def is_boot(self, name: str) -> bool:
        if name in self.boot:
            return True
        host = name.split("$$", 1)[0]
        return host != name and host in self.boot

    def app_class_names(self) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for s in self.sources[: self.boot_start]:
            for n in s.names():
                if n not in seen:
                    seen.add(n)
                    out.append(n)
        return out

    def services(self) -> dict[str, list[str]]:
        res: dict[str, list[str]] = {}
        for s in self.sources[: self.boot_start]:
            for k, v in s.services().items():
                res.setdefault(k, []).extend(v)
        return res
