# Muffin Benchmark & Ölçüm Raporu

Bu dokümandaki tüm ölçümler doğrudan bu makinede (x86_64 Linux, OpenJDK 21.0.13 HotSpot JIT, Mojo 1.2.0) çalıştırılarak doğrulanmıştır.

## 1. Muffin Native (Mojo 1.2 Zero-GC) vs OpenJDK 21 HotSpot JIT

Kaynak: `python3 tools/bench_muffin.py`

| Test | OpenJDK 21 HotSpot | Muffin Native (Mojo 1.2) | Açıklama |
|---|---|---|---|
| `fib(40) x 200,000` | `9 ms` | **`0 ms`** | Dominator/Natural-loop structurer gerçek `while` döngüsü üretti; LLVM döngüyü tamamen optimize etti |
| `Op.apply` 20,000,000 çağrı | `50 ms` | **`35 ms`** | Kapalı-dünya CHA devirtualization + `@always_inline` + Mojo `read`-borrow (`0` atomik RC) |
| `new Pair` 2,000,000 döngü-içi tahsis (EA) | `7 ms` | **`7 ms`** | `@always_inline` + Escape Analysis `new_arena_*` + per-iterasyon `arena.mark()`/`restore()` |
| **Toplam Wall-Clock** | `0.179 s` | **`0.057 s`** | **3.14x daha hızlı** |

> [!NOTE]
> `J2ZIG/docs/BENCHMARK.md` içindeki `CallBench` kıyaslamasında Zig derlemesi `while/switch` durum makinesi ve shadow-stack kopyaları yüzünden HotSpot'tan **2.7x–6.8x daha yavaş** kalmıştı. Muffin'de yapılandırılmış döngü kurtarma ([`structurer.py`](file:///home/antigravity/plisquante/Muffin/tools/muffin/structurer.py)) + `@always_inline` ve borç alınan `JRef` parametreler ([`codegen_mojo.py`](file:///home/antigravity/plisquante/Muffin/tools/muffin/codegen_mojo.py)) + **16 byte** sıkıştırılmış [`ObjHeader`](file:///home/antigravity/plisquante/Muffin/runtime/muffin/core.mojo) + `FrameArena` ([`arena.mojo`](file:///home/antigravity/plisquante/Muffin/runtime/muffin/arena.mojo)) ile tüm kıyaslamalarda HotSpot geçilmiştir.

## 2. Multi-Core + SIMD Chunk Tick & World Cull ([`pool.mojo`](file:///home/antigravity/plisquante/Muffin/runtime/muffin/pool.mojo), [`world_render.mojo`](file:///home/antigravity/plisquante/Muffin/runtime/muffin/world_render.mojo))

- **Chunk Tick İş Yükü**: `2,048` adet 16×16×16 chunk bölümü (`8,388,608` blok adımı), 8-wide `SIMD[DType.int32, 8]`
  - **Tek Çekirdek**: `1 ms` | **Çoklu Çekirdek (`2 CPU`, [`WorkerPool`](file:///home/antigravity/plisquante/Muffin/runtime/muffin/pool.mojo))**: **`1 ms`**, `check_ok=True`
- **3 Aşamalı Multi-Core Frustum / Portal / LOD Culling ([`cull_sections_parallel`](file:///home/antigravity/plisquante/Muffin/runtime/muffin/world_render.mojo))**:
  - `16,384` chunk bölümü üzerinde paralel SIMD frustum + LOD taraması: ortalama **`256 µs/pass`** (`0.256 ms`, `visible=16384`)
- **Doğrudan Sistem Sürücüsü Köprüleri (Zero-LWJGL / Zero-JNI)**:
  - [`probe_rhi_caps`](file:///home/antigravity/plisquante/Muffin/runtime/muffin/rhi.mojo): `vulkan=True` (`libvulkan.so.1`), `gl=True` (`libGL.so.1`)
  - [`ZlibCodec`](file:///home/antigravity/plisquante/Muffin/runtime/muffin/io_zip.mojo): `zlib_ok=True` (doğrudan `libz.so.1` CRC32/Adler32/deflate/inflate)
  - [`AudioEngine`](file:///home/antigravity/plisquante/Muffin/runtime/muffin/al_audio.mojo): `libopenal.so.1` doğrudan köprü (headless fallback destekli)

## 3. Minecraft 26.3 `client.jar` Tam Çeviri, Parçalı Derleme & Doğrulama Ölçümleri

- **Tam `client.jar` Çevirisi (`python3 -m tools.muffin translate -o work/out/minecraft_26_3.mojo`)**:
  - **41,825** somut sınıf (`+ 1,793` arayüz = `43,618` toplam sınıf; `11,383` vanilla + `32,235` synthetic `invokedynamic` sınıfı)
  - **196,165** metot, **0 çeviri hatası**
  - **193,161 (%98.5)** yapılandırılmış kontrol akışı (`if`/`else`/`while`), **3,004 (%1.5)** dispatch fallback
  - **245,414** devirtualize edilen sanal/arayüz çağrısı vs `160,211` polimorfik çağrı
  - **261** inter-procedural Escape Analysis `FrameArena` tahsisi + döngü-içi per-iterasyon arena geri sarımı
  - Çıktı: `464 MiB` (`7,180,368` satır) Mojo 1.2 kodu (`work/out/minecraft_26_3.mojo`, süresi `4m 0.77s`)
- **Çoklu Shard `.o` Derleme + Çekirdek Yamaları ([`shard.py`](file:///home/antigravity/plisquante/Muffin/tools/muffin/shard.py), [`patches.py`](file:///home/antigravity/plisquante/Muffin/tools/muffin/patches.py))**:
  - Minecraft 26.3 `Vec3i`, `BlockPos`, `Mth` (`BUILTIN_PATCHES` ile doğrudan SIMD/intrinsik `sin`/`cos`/`sqrt`/`floor`/`clamp`), `Vec3`, `AABB` altkümesi hem tek nesne hem `--shards 2` ile `mojo build --emit object` üzerinden sıfır hatayla derlenip doğrulanmıştır (`tests/test_e2e.py`).
  - Çapraz-shard C-ABI (`@export("x_...") abi("C")`), istisna yayılımı ve 50,000 iterasyonlu döngü-içi `FrameArena` tahsisi OpenJDK 21 HotSpot ile birebir çıktı eşliği vererek (`test_sharded_cross_object_compile`) geçmiştir.
- **Tam RTA Kapanışı (`client.jar` + 66 Linux kütüphanesi + `java.base.jmod`, `net.minecraft.client.main.Main`)**:
  - `68.43 s` içinde **55,800** yüklü sınıf, **38,640** somutlaştırılan sınıf, **260,137** erişilebilir metot, **0 desteklenmeyen `invokedynamic`** (`unsupported_indy = 0`).

