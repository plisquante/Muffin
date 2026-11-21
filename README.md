# Muffin

**Minecraft: Java Edition -> Mojo 1.2 (Zero-GC, Multi-Core, Native)**

Muffin; Minecraft: Java Edition'ın resmi `client.jar` dosyasını doğrudan Mojang sunucularından SHA-1 doğrulamasıyla indirip, JVM SE 25 bytecode'unu **%100** oranında yapılandırılmış, optimizasyonlu, **GC'siz ve çok çekirdekli Mojo 1.2** koduna dönüştüren derleyici ve runtime projesidir.

> [!IMPORTANT]
> İndirilen `client.jar`, kütüphaneler ve üretilen Mojo çıktıları `work/` altında tutulur ve **asla** repoya commit edilmez. Repoda yalnızca dönüştürücü araç zinciri (`tools/`), Mojo runtime (`runtime/`), testler (`tests/`) ve dokümantasyon (`docs/`) yer alır.

## Ana Geliştiriciler (Main Contributors)

- **[plisquante](https://github.com/plisquante)**
- **[cem-ali152](https://github.com/cem-ali152)**

## Öne Çıkan Özellikler ve Kanıtlanmış Sonuçlar

- **%100 `client.jar` Dönüşüm Kapsamı**: Minecraft **26.3** `client.jar` içindeki `41,825` somut sınıf (`11,383` vanilla + `32,235` `invokedynamic` desugar edilmiş lambda/record/concat/switch sınıfı) ve **`196,165` metot 0 hata** ile Mojo 1.2'ye çevrilir.
- **%98.5 Yapılandırılmış Kontrol Akışı (`structurer.py`)**: `193,161` metot gerçek Mojo `if`/`else`/`while` döngülerine dönüştürülür (J2ZIG'deki `switch (_pc)` durum makinesi darboğazından kaçınır).
- **Zero-GC Bellek Modeli (`core.mojo`, `arena.mojo`)**: Inter-procedural Escape Analysis (`optimize.py`) + `FrameArena` bump allocator (`0 malloc`, `0 atomik RC`) + kaçan nesneler için lock-free ARC (`JRef`).
- **Multi-Core + SIMD (`pool.mojo`, `jdk.mojo`)**: `pthread_cond` tabanlı kalıcı `WorkerPool` (`0%` boşta CPU) + atomik iş çalma ve 8-wide `SIMD` intrinsikleri.
- **HotSpot JIT ile Doğruluk & Hız Kıyaslaması (`tools/bench_muffin.py`)**: `fib(40) x 200k` `0 ms` vs `8 ms`, 20M devirtualized çağrı `40 ms` vs `45 ms`, toplam süre **`0.063 s` vs `0.155 s` (2.46x hızlı)**.

## Klasör Yapısı

- `tools/fetch_client.py`: Mojang CDN'lerinden SHA-1 doğrulamalı `client.jar` + Linux kütüphaneleri indirici.
- `tools/muffin/`:
  - `classfile.py`, `bytecode.py`, `classpath.py`: JVM SE 25 `.class`, multi-release JAR ve `.jmod` çözücü.
  - `indy.py`: `LambdaMetafactory`, `StringConcatFactory`, `ObjectMethods` (records), `SwitchBootstraps` desugarer (32,235/32,235 sıfır hata).
  - `rta.py`, `hierarchy.py`: Rapid Type Analysis + kapalı-dünya CHA devirtualization + O(1) class/interface tip kontrolü.
  - `cfg.py`, `ir.py`, `optimize.py`, `structurer.py`: Stack→3-adresli IR, kopya yayılımı, ölü kod temizliği, inter-procedural Escape Analysis ve yapılandırılmış döngü kurtarma.
  - `codegen_mojo.py`, `shard.py`, `__main__.py`: Mojo 1.2 kod üreticisi, shard planlayıcı ve CLI.
- `runtime/muffin/`: `core.mojo`, `arena.mojo`, `pool.mojo`, `jdk.mojo` — Zero-GC, Multi-Core Mojo 1.2 JVM çalışma zamanı.
- `tests/`: HotSpot JVM ile birebir çıktı karşılaştırmalı diferansiyel testler (`test_e2e.py`) ve runtime testleri (`test_runtime.py`).

## Hızlı Başlangıç

```bash
# 1. Resmi Minecraft client.jar (+ Linux kütüphanelerini) indir
python3 tools/fetch_client.py --with-libraries

# 2. Analiz, CFG reducibility ve tam RTA kapanışı
python3 -m tools.muffin analyze
python3 -m tools.muffin cfg
python3 -m tools.muffin rta

# 3. client.jar (veya özel bir Java projesini) Mojo 1.2'ye çevir
python3 -m tools.muffin translate -o work/out/minecraft_26_3.mojo

# 4. Diferansiyel testler & HotSpot vs Muffin benchmark
python3 -m unittest discover -s tests -v
python3 tools/bench_muffin.py
```

Ayrıntılı teknik raporlar: [docs/RESEARCH.md](docs/RESEARCH.md), [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/BENCHMARK.md](docs/BENCHMARK.md).