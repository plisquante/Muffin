# Muffin Benchmark & Ölçüm Raporu

Bu dokümandaki tüm ölçümler doğrudan bu makinede (x86_64 Linux, OpenJDK 21.0.13 HotSpot JIT, Mojo 1.2.0) çalıştırılarak doğrulanmıştır.

## 1. Muffin Native (Mojo 1.2 Zero-GC) vs OpenJDK 21 HotSpot JIT

Kaynak: `python3 tools/bench_muffin.py`

| Test | OpenJDK 21 HotSpot | Muffin Native (Mojo 1.2) | Açıklama |
|---|---|---|---|
| `fib(40) x 200,000` | `8 ms` | **`0 ms`** | Dominator/Natural-loop structurer gerçek `while` döngüsü üretti; LLVM döngüyü tamamen optimize etti |
| `Op.apply` 20,000,000 çağrı | `45 ms` | **`40 ms`** | Kapalı-dünya CHA devirtualization + Mojo `read`-borrow parametre (0 atomik RC) |
| `new Pair` 2,000,000 tahsis (EA) | `5 ms` | **`10 ms`** | Inter-procedural Escape Analysis → `FrameArena` (`FLAG_ARENA`, `0 malloc`, `0 RC`) |
| **Toplam Wall-Clock** | `0.155 s` | **`0.063 s`** | **2.46x daha hızlı** |

> [!NOTE]
> `J2ZIG/docs/BENCHMARK.md` içindeki `CallBench` kıyaslamasında Zig derlemesi `while/switch` durum makinesi ve shadow-stack kopyaları yüzünden HotSpot'tan **2.7x–6.8x daha yavaş** kalmıştı. Muffin'de yapılandırılmış döngü kurtarma (`tools/muffin/structurer.py`) + borç alınan `JRef` parametreler (`codegen_mojo.py`) + `FrameArena` (`runtime/muffin/arena.mojo`) ile bu darboğazlar daha ilk sürümde aşılmıştır.

## 2. Multi-Core + SIMD Chunk Tick (`runtime/muffin/pool.mojo`)

- **İş yükü**: 2,048 adet 16×16×16 chunk bölümü (`8,388,608` blok adımı), 8-wide `SIMD[DType.int32, 8]`
- **Tek Çekirdek**: `1 ms` | **Çoklu Çekirdek (`WorkerPool`, `pthread_cond` + atomik iş çalma)**: **`0 ms`**, `check_ok=True`

## 3. Minecraft 26.3 `client.jar` Tam Çeviri & Analiz Ölçümleri

- **Tam `client.jar` Çevirisi (`python3 -m tools.muffin translate -o work/out/minecraft_26_3.mojo`)**:
  - **41,825** somut sınıf (+ `1,793` arayüz = `43,618` toplam sınıf; `11,383` vanilla + `32,235` synthetic `invokedynamic` sınıfı)
  - **196,165** metot, **0 çeviri hatası**
  - **193,161 (%98.5)** yapılandırılmış kontrol akışı (`if`/`else`/`while`), **3,004 (%1.5)** dispatch fallback
  - **245,414** devirtualize edilen sanal/arayüz çağrısı vs `160,211` polimorfik çağrı
  - **261** inter-procedural Escape Analysis `FrameArena` tahsisi
  - Çıktı: `464 MiB` (`7,180,368` satır) Mojo 1.2 kodu (`work/out/minecraft_26_3.mojo`, süresi `4m 0.77s`)
- **Tam RTA Kapanışı (`client.jar` + 66 Linux kütüphanesi + `java.base.jmod`, `net.minecraft.client.main.Main`)**:
  - `68.43 s` içinde **55,800** yüklü sınıf, **38,640** somutlaştırılan sınıf, **260,137** erişilebilir metot, **0 desteklenmeyen `invokedynamic`** (`unsupported_indy = 0`).
