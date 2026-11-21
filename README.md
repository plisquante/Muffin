# Muffin

**Minecraft: Java Edition -> Mojo 1.2 (Zero-GC, Multi-Core, Native)**

Muffin; Minecraft: Java Edition'in resmi `client.jar` dosyasini dogrudan Mojang sunucularindan indirip SHA-1 ile dogrulayan, JVM bytecode'unu %100 yapilandirilmis (structured) IR uzerinden temiz **Mojo 1.2** koduna ceviren ve ardindan agir optimizasyonlar (GC kaldirma / ARC + arena bellek modeli, devirtualization, single-core dongulerin multi-core is dagitimina ve SIMD'e cevrilmesi) uygulayan derleyici + runtime projesidir.

> [!IMPORTANT]
> Indirilen `client.jar`, kutuphaneler ve cevrilmis Mojo ciktilari `work/` altinda tutulur ve **asla** repoya commit edilmez. Repoda yalnizca arac zinciri (`tools/`), Mojo runtime (`runtime/`) ve dokumantasyon (`docs/`) bulunur.

## Ana Gelistiriciler (Main Contributors)

- **[plisquante](https://github.com/plisquante)**
- **[cem-ali152](https://github.com/cem-ali152)**

## Neden Mojo 1.2?

- **Garbage Collector yok (Zero-GC)**: Stop-the-world GC duraksamalari yerine derleme zamaninda Escape Analysis + Frame/Tick Arena + kilit-siz atomik referans sayaci (`JRef` / `ObjHeader`).
- **Single-Core -> Multi-Core**: Tek cekirdekli tick/chunk donguleri yerine `runtime/muffin/pool.mojo` uzerinden native OS thread havuzu (`parallel_for_range`) ve SIMD vektorizasyonu.
- **Kapali-Dunya Devirtualization (CHA)**: `client.jar` icindeki sanal/arayuz metotlarin **%91.0**'i yalnizca tek somut gerceklestirime sahip; dogrudan statik cagri + LLVM inline'a donusturuluyor.

## Mimari & Klasor Yapisi

- `tools/fetch_client.py`: Mojang manifestinden `client.jar` + Linux kutuphanelerini SHA-1 kontroluyle `work/` altina indirir.
- `tools/muffin/classfile.py`: Sifir bagimlilikli JVM SE 25 (v69) `.class` cozumleyici.
- `tools/muffin/bytecode.py`: Tam JVM opcode cozumleyici.
- `tools/muffin/analyze.py`: Jar ici sinif, `invokedynamic`, concurrency, reflection ve dis API yuzeyi analizi.
- `tools/muffin/cfg.py`: Kontrol akisi grafigi (CFG) ve reducibility kanitlayici.
- `tools/muffin/hierarchy.py`: Sinif hiyerarsisi analizi (CHA), O(1) `instanceof` araliklari ve devirtualization.
- `tools/muffin/ir.py`: JVM stack -> register 3-adresli IR donusturucu.
- `tools/muffin/codegen_mojo.py`: Temiz Mojo 1.2 kod ureticisi.
- `runtime/muffin/core.mojo`: Zero-GC `JRef`, `ObjHeader`, `JArrayData`, `JStringData`, `JThrowable`.
- `runtime/muffin/pool.mojo`: `pthread` + atomik is dagitimli multi-core `parallel_for_range`.
- `tests/test_e2e.py`: Gercek Java -> `javac` -> Muffin -> `mojo run` uctan uca dogruluk testleri.

## Hizli Baslangic

```bash
# 1. Resmi client.jar (+ kutuphaneleri) indir
python3 tools/fetch_client.py --with-libraries

# 2. client.jar analizi & CFG reducibility olcumu
python3 -m tools.muffin analyze
python3 -m tools.muffin cfg

# 3. Java siniflarini Mojo 1.2'ye cevir
python3 -m tools.muffin translate -i <jar_or_classes> -o work/out.mojo --entry <MainClass>
mojo run -I . work/out.mojo

# 4. Uctan uca testleri calistir
python3 -m unittest discover -s tests -v
```

Detayli olcumler ve teknik kararlar icin: [docs/RESEARCH.md](docs/RESEARCH.md) ve [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).