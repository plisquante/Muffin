# Muffin Teknik Mimarisi

## 1. 5 Asamali Ceviri & Optimizasyon Hatti

```
  client.jar (Java 25, unobfuscated)
       │
       ▼  [1] classfile.py + bytecode.py
  Lossless ClassFile & Opcode Stream
       │
       ▼  [2] hierarchy.py (CHA)
  O(1) Subtype Intervals [id, max_sub_id] + %91 Devirtualization Haritasi
       │
       ▼  [3] cfg.py + ir.py
  Reducible CFG + Forward Stack-Frame Typing -> Tipli 3-Adresli IR
       │
       ▼  [4] Optimizasyon Gecisleri (Planlanan & Aktif)
  Devirtualization · Escape Analysis (Heap->Stack) · Arena Tagging · SIMD Loop Vectorization · Multi-Core Task Splitting
       │
       ▼  [5] codegen_mojo.py + runtime/muffin/
  Temiz, Zero-GC, Multi-Core Mojo 1.2 Kaynak Kodu -> Native Binary (`mojo build`)
```

## 2. Zero-GC Bellek Modeli (`runtime/muffin/core.mojo`)

Java'da her `new` cagrisi heap uzerinde GC tarafindan izlenir; oyun dongusu sirasinda frame basina yuzbinlerce gecici nesne (`BlockPos`, `Vec3`, `AABB`, `Iterator`) uretilip GC duraksamalarina yol acar. Muffin bunu 3 katmanda cozer:

1. **Katman 1 — Stack / Value Lowering (Escape Analysis)**: Metodun disina kacmayan nesneler ve `record` degerleri heap'e hic cikmadan stack `struct` olarak tutulur (`rc = 0`, `malloc = 0`).
2. **Katman 2 — Tick / Frame Arena**: Tek kare veya tek tick omurlu geciciler lineer bump-allocator arenasindan ayrilir ve frame sonunda O(1) sifirlanir.
3. **Katman 3 — Lock-Free ARC (`JRef` + `ObjHeader`)**: Gercekten paylasilan uzun omurlu nesneler (dunyanin parcalari, entity'ler, chunk'lar) `Atomic[Int64]` referans sayacina sahip `JRef` ile yonetilir; sayac 0'a dustugu anda deterministik `drop` ile serbest birakilir.

## 3. Single-Core -> Multi-Core Mimari (`runtime/muffin/pool.mojo`)

Minecraft'in klasik tek-cekirdek darbogazlari:
- **Chunk Section Meshing / Lighting**: Birbirinden bagimsiz 16x16x16 bolumler `parallel_for_range` ile tum donanim thread'lerine dinamik atomik bloklarla (`fetch_add(grain)`) dagitilir.
- **Entity & Block-Entity Tick**: Salt-okunur dunya gorunumu uzerinde paralel hesaplama + deterministik birlestirme (double-buffered state).
- **Mojo 1.2 Global State Kurali**: Mojo 1.2'de degistirilebilir modul-duzeyi global degisken yoktur. Tum Java `static` alanlari tek bir `JStatics` yapisinda toplanir ve her dunya/istemci ornegine acikca tasinir; bu sayede thread guvenligi ve coklu izole dunya calistirma dogal olarak saglanir.
