# flow5 7.57 API runner

Bu klasör, AeroOpt'un `flow5-native` modunu gerçek flow5 analizlerine bağlayan küçük C++20 süreç köprüsüdür. `flow5.exe` masaüstü uygulamasının yolu bu amaçla kullanılamaz; resmî otomasyon yüzeyi flow5'in C++ API kütüphaneleridir.

Son kullanıcı için önerilen yol, depo kökündeki `Build Windows EXE with flow5 7.57` GitHub Actions iş akışıdır. İş akışı sabitlenmiş bağımlılıkları bulutta derler, gerçek flow5 smoke testlerini çalıştırır ve runner'ı `AeroOpt.exe` paketinin içine koyar. Bu durumda yerel SDK kurulumu veya runner yolu girmek gerekmez.

Runner iki işlem yapar:

- `foil`: DAT profilini yükler; her Reynolds/Mach noktası için `XFoilTask` ile Tip-1 polar çözer.
- `wing`: Bir veya üç DAT profiliyle üç-istasyonlu plane XML'ini yükler; `PlaneTask` ile LLT/VLM/panel polarlarını ve gömülü XFoil üzerinden viskoz drag'i çözer; istenirse projeyi `.fl5` olarak kaydeder.

Kanat yanıtı yalnız toplam katsayıları değil, eşleşen gerçek `PlaneOpp/WingOpp` çalışma noktasından `out_of_mesh`, viskoz yakınsama oranı, panel sayıları, `Cp_min` ve spanwise yerel Cl/yük/Re/CDi/CDv/eğilme/twist dağılımını da döndürür. AeroOpt bu telemetriyi mesh yakınsaması, yapısal ön tarama ve su kavitasyon taramasında kullanır.

## Uyumlu sürüm

Bu kaynak **flow5 v7.57** API'sine ve `a9e852c559590188e00e9efe997c35c1dec7209b` commit'ine sabitlenmiştir. Daha yeni bir sürümde API isimleri veya çalışma noktası yapıları değişebilir. Runner'ın bildirdiği sürüm, derlerken `-DFLOW5_VERSION=7.57` ile ayarlanır ve Python tarafı başka üretim sürümünü reddeder.

## Windows derleme özeti

Gerekenler:

1. Visual Studio 2022 “Desktop development with C++”.
2. CMake 3.20+.
3. flow5 v7.57 kaynak kodu ve onun başarılı **Release** derlemesi.
4. flow5'i derlerken kullandığınız aynı Qt 6, Gmsh SDK, OpenCascade ve Intel oneAPI MKL kurulumu.

Önce kaynak sürümünü sabitleyin ve flow5'in kendi `API_examples/PlaneRun1` örneğini derleyebildiğinizi doğrulayın:

```powershell
git clone https://github.com/techwinder/flow5.git C:\dev\flow5-7.57
cd C:\dev\flow5-7.57
git checkout a9e852c559590188e00e9efe997c35c1dec7209b
```

Intel oneAPI Command Prompt kullanmıyorsanız CMake'den önce oneAPI `setvars.bat` betiğini çalıştırın; `find_package(MKL)` ve `find_package(TBB)` aynı flow5 yapılandırmasını bulmalıdır.

“x64 Native Tools Command Prompt for VS 2022” içinde örnek:

```powershell
powershell -ExecutionPolicy Bypass -File .\flow5_bridge\build_windows.ps1 `
  -Flow5Source C:\dev\flow5-7.57 `
  -Flow5Build C:\dev\build-flow5-7.57 `
  -QtRoot C:\Qt\6.9.1\msvc2022_64 `
  -GmshRoot C:\SDK\gmsh-4.14.1-Windows64-sdk `
  -OccInclude C:\SDK\OpenCASCADE-7.9.2\inc `
  -BuildDir C:\dev\build-aeropt-flow5-runner
```

Çıktı normalde:

```text
C:\dev\build-aeropt-flow5-runner\Release\aeropt-flow5-runner.exe
```

Flow5, Qt, Gmsh, MKL ve TBB DLL'lerinin runner tarafından bulunabilmesi gerekir. En temiz yöntem, flow5'in çalışabildiği Release klasöründeki aynı DLL'leri runner EXE'sinin yanına koymak veya ilgili `bin` klasörlerini `PATH` değişkenine eklemektir.

Arayüzde **flow5 runner yolu** alanına `aeropt-flow5-runner.exe` yolunu girin; `flow5.exe` yolunu girmeyin.

## Linux

flow5/XFoil başlık ve kütüphaneleri kurulduktan sonra:

```bash
cmake -S flow5_bridge -B build-flow5-runner \
  -DFLOW5_SOURCE_DIR=/path/to/flow5-7.57 \
  -DFLOW5_BUILD_DIR=/path/to/build-flow5-7.57 \
  -DGMSH_ROOT=/usr \
  -DOCC_INCLUDE_DIR=/usr/include/opencascade
cmake --build build-flow5-runner -j16
```

Dağıtımınızdaki OpenBLAS hedef adı farklıysa CMake bağlantı satırını flow5'in kendi `API_examples` CMake dosyasıyla aynı hale getirin.

## Lisans

Bu köprü flow5 kütüphanelerine bağlandığı için **GPL-3.0-or-later** lisanslıdır. Python web uygulaması ayrı bir süreç üzerinden haberleşir ve MIT lisansını korur. Kaynak arşivi üçüncü taraf ikililerini içermez; GitHub Actions Windows artifact'i gerekli runtime dosyalarını ayrıca paketler.
