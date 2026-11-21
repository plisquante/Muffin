# Muffin Teknik Mimarisi

## 1. 6 Aşamalı Çeviri & Optimizasyon Hattı

```
  client.jar + 66 libs + JDK .jmod
       │
       ▼  [1] classpath.py + classfile.py + bytecode.py
  Multi-Release JAR/JMOD Çözümleme (LWJGL -> r11 JNI) + Kayıpsız JVM SE 25 Opcode Akışı
       │
       ▼  [2] indy.py
  %100 invokedynamic Desugaring (LambdaMetafactory, StringConcatFactory,
                                 ObjectMethods record, SwitchBootstraps)
       │
       ▼  [3] rta.py + hierarchy.py (CHA)
  Rapid Type Analysis + O(1) Class/Interface Tip Kontrolü + Devirtualization Haritası
       │
       ▼  [4] cfg.py + ir.py + optimize.py
  Forward Stack-Frame Tip Çıkarımı -> 3-Adresli IR -> Kopya Yayılımı +
  Ölü Değişken Temizliği + Inter-Procedural Escape Analysis (FrameArena)
       │
       ▼  [5] structurer.py
  Dominator + Natural-Loop Yapılandırılmış Kontrol Akışı (%98.5 gerçek if/else/while)
       │
       ▼  [6] codegen_mojo.py + shard.py + runtime/muffin/
  Temiz, Zero-GC, Multi-Core Mojo 1.2 Kaynak Kodu -> Native Binary (`mojo build`)
```

## 2. J2ZIG İncelemesinden Alınan Kritik Dersler

`plisquante/J2ZIG` projesinin gerçek Minecraft `26.3-client.jar` üzerinde çözdüğü problemler ve benchmark darboğazları (`J2ZIG/docs/BENCHMARK.md`, `J2ZIG/docs/STATUS.md`) incelenerek Muffin baştan şu 5 kurala göre tasarlandı:

1. **Durum Makinesi (`while/switch _pc`) Yerine Yapılandırılmış Döngüler (`structurer.py`)**: J2ZIG tüm çok bloklu metotları `switch (_pc)` içine koyduğu için LLVM döngü sayaçlarını register'da tutamıyor ve `CallBench`'te HotSpot'un 2.7x–6.8x gerisinde kalıyordu. Muffin metotların **%98.5**'ini gerçek Mojo `while`/`if`/`else` yapılarına kurtarır.
2. **Shadow-Stack & Kopya Trafiği Yerine Mojo `read`-Borrow + `FrameArena`**: `JRef` parametreleri varsayılan Mojo `read` borrow konvansiyonuyla taşınır (`0` atomik `fetch_add`/`fetch_sub`), kaçmayan tahsisler ise inter-procedural Escape Analysis ile `FLAG_ARENA` bayraklı `FrameArena`'ya iner.
3. **`%100` `invokedynamic` Desugaring (`indy.py`)**: `client.jar`'daki tüm 32,235 `invokedynamic` çağrı noktası (`LambdaMetafactory`, `StringConcatFactory`, `ObjectMethods`, `SwitchBootstraps`) `Code.insns_override` üzerinden doğrudan IR'a desugar edilir.
4. **Multi-Release LWJGL Seçimi (`classpath.py`)**: `lwjgl` JAR'ları için `release=11` seçilerek Java 25 FFM (`java.lang.foreign`) dallanması yerine temiz JNI/C ABI arayüzü alınır.
5. **Kalıcı `WorkerPool` (`runtime/muffin/pool.mojo`)**: İşçi thread'ler bir kez oluşturulup `pthread_cond` üzerinde `%0` CPU ile uyutulur ve atomik blok çalma (`fetch_add(grain)`) ile uyanır.

## 3. Zero-GC Bellek Modeli (`runtime/muffin/core.mojo`, `arena.mojo`)

1. **Katman 1 — `FrameArena` (Escape Analysis)**: Inter-procedural özetle kaçmadığı ispatlanan nesne ve diziler `FLAG_ARENA` ile 256 KiB slab arenada `arena.mark()` / `arena.restore()` içinde ayrılır (`0 malloc`, `0 free`, `0 atomik RC`).
2. **Katman 2 — Lock-Free ARC (`JRef` + `ObjHeader`)**: Kaçan uzun ömürlü nesneler `Atomic[Int64]` sayaçla deterministik serbest bırakılır; stop-the-world GC duraksaması `0.0 ms`'dir.
3. **Katman 3 — Mojo 1.2 Global State Modeli**: Mojo 1.2'de değiştirilebilir modul-düzeyi `var` olmadığı için tüm Java `static` alanları ve thread-local arena tek `JStatics` yapısında taşınır — thread güvenliği ve çoklu izole dünya desteği doğal olarak sağlanır.
