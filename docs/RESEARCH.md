# Muffin Arastirma Notlari (Minecraft 26.3 `client.jar`)

Bu dokuman, Minecraft: Java Edition **26.3** (`client.jar`, SHA-1 `e877b6a07acd633fb3bb475002175cec036e7b87`) uzerinde `tools/muffin` ile yapilan gercek olcumlere dayanir.

## 1. Olcek ve Bytecode Profili

| Metrik | Deger | Derleyiciye Etkisi |
|---|---|---|
| Toplam `.class` | **11,383** (v69 / Java 25) | Mojang 26.1+ itibariyle **obfuscation kaldirildi**; isimler temiz! |
| Interface / Abstract | 1,793 / 445 | Kapali-dunya CHA ile yuksek oranda devirtualize edilebilir |
| Record / Enum / Sealed | 2,014 / 576 / 50 | `record` siniflari deger tipine (`struct`) indirilmeye ideal |
| Toplam Metot (kodlu) | 98,739 (95,427 kodlu) | 95,427/95,427 LineNumberTable, 88,698 LVT iceriyor |
| Native metot (`client.jar` ici) | **0** | Minecraft kodunun icinde JNI yok; native cagilar yalnizca LWJGL/Oshi tarafinda |
| `synchronized` metot | **38** | Kilit sayisi cok dusuk; atomik/mutex sarmalama kolay |
| `volatile` alan | **122** | `std.atomic.Atomic` ile birebir eslenir |
| Bytecode / Komut | 4.13 MB / 1,910,975 insn | Makul boyut; LLVM/MLIR ile paralel derlenebilir |
| Exception handler | 3,929 | Mojo'nun sifir-maliyetli tipli `raises JThrowable` mekanizmasi ile eslenir |

## 2. Kritik Bulgular (Tahmin Degil, Kanit)

### A. Irreducible CFG = 0 / 95,427
`python3 -m tools.muffin cfg` calistirmasi **95,427 metodun tamaminda 0 irreducible CFG** buldu. Mojo'da `goto` yoktur, ancak kontrol akisi %100 yapilandirilabilir (structured `if` / `else` / `while`).

### B. %91.0 Devirtualization Orani
Kapali-dunya hiyerarsi analizi (`tools/muffin/hierarchy.py`):
- 60,895 sanal/arayuz metottan **55,400 tanesi (%91.0)** tek bir somut gerceklestirime sahip.
- Bu cagrilar dogrudan statik Mojo fonksiyon cagrisina indirgenip LLVM tarafindan inline edilebiliyor.

### C. `invokedynamic` Dagilimi (32,235 cagri noktasi)
- `LambdaMetafactory`: **24,014** — ceviri sirasinda closure/struct + fonksiyon pointer'ina desugar edilir (dinamik bytecode uretimi gerekmez).
- `ObjectMethods` (record `equals`/`hashCode`/`toString`): **5,941** — dogrudan uretilir.
- `StringConcatFactory`: **2,201** — Mojo `String` formatlamasina indirgenir.
- `SwitchBootstraps` (pattern switch): **79** — deterministik dallanmaya acilir.

### D. Reflection Neredeyse Sifir
Tum `client.jar` genelinde yalnizca ~20 `java.lang.reflect` / `MethodHandle` cagrisi var. Statik derleme modelini bozan dinamik sinif yukleme sorunu yok.

## 3. Harici Kutuphane Yuzeyi (66 Linux JAR, 31,663 sinif)

1. **Veri yapilari & yardimcilar** (`fastutil` 12,965 sinif, `guava` 1,969 sinif, `commons-lang3`): Bunlarin tamamini Java'dan cevirmek yerine Mojo-native karsiliklar (`List`, `Dict`, `BitSet`, SIMD-hizli primitive haritalar) kullanmak kat kat hizli ve temizdir.
2. **Grafik / Ses / Pencere** (`lwjgl-vulkan`, `lwjgl-opengl`, `lwjgl-sdl`, `lwjgl-openal`): Bunlar zaten C kutuphanelerinin ince JNI sarmalayicilaridir. Mojo'da JNI yerine dogrudan `std.ffi.external_call` ile C ABI'ye baglanir (JNI overhead = 0).
3. **Matematik** (`org.joml`): Mojo'nun yerlesik `SIMD[DType.float32, 4]` ve `SIMD[DType.float64, 4]` vektorlerine dogrudan derlenir.
4. **Ag / Codec / Serialization** (`netty`, `datafixerupper`, `brigadier`, `gson`): Asamali olarak native Mojo modulleri + secici bytecode ceviri ile kapsanir.

## 4. Neden J2ZIG Gibi Hatalara Dusmuyoruz?

1. **GC emulasyonu yapmiyoruz**: Java'nin mark-sweep GC'sini baska bir dile tasimak hem yavas hem karmasiktir. Bunun yerine deger tipleri + Arena + deterministik ARC (`JRef`) kullaniyoruz.
2. **Line-by-line bytecode ceviri spagettisi yok**: Stack simulasyonunu 3-adresli tipli IR'a kaldirip, devirtualization ve olu kod temizligi yapiyoruz.
3. **JNI/Reflection kopruleri yok**: LWJGL gibi C tabanli kutuphaneler Mojo `external_call` ile dogrudan C fonksiyonunu cagirir.
