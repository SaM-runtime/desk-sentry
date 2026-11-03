# Desk Sentry 第三方聲明

本檔是建置成品的第三方元件盤點，不構成法律意見。表格中的版本由 PyInstaller 完成收集後，從保留的 DLL／PYD 資源與已安裝 wheel metadata 讀取；每次建置都會重新產生散布版本。`THIRD_PARTY_INVENTORY.json` 列出各原生檔案的相對路徑、資源版本與 SHA-256。空白版本表示該檔案沒有字串版本資源，並非宣稱其上游版本已知。

<!-- build-inventory:start -->

| Retained component | Observed version(s) |
| --- | --- |
| CPython | 3.12.10 |
| Microsoft MSVC / UCRT | 10.0.17134.12 (WinBuild.160101.0800), 10.0.22621.1778 (WinBuild.160101.0800), 14.40.33810.0, 14.42.34438.0, 14.44.35211.0 |
| OpenSSL | 3.0.16, 3.0.21 |
| Qt / PySide6 | 6.11.1.0 |
| Wheel/build tool: PySide6-Essentials | 6.11.1 |
| Wheel/build tool: shiboken6 | 6.11.1 |
| Wheel/build tool: numpy | 2.5.1 |
| Wheel/build tool: opencv-python-headless | 5.0.0.93 |
| Wheel/build tool: PyInstaller | 6.21.0 |

<!-- build-inventory:end -->

## CPython 與內含函式庫

- 授權：PSF License Version 2 與 Python 歷史聲明。建置環境的完整文字位於 `_internal/third_party_licenses/python/LICENSE.txt`。
- 原始碼與授權：<https://github.com/python/cpython>、<https://docs.python.org/3.12/license.html>。
- 保留的 CPython extension 包含 libffi（MIT）、Expat（MIT）、bzip2（BSD-style）、liblzma（0BSD／public domain）與 mpdecimal（BSD-2-Clause）。Python 授權檔內含相關文字與來源；libffi 授權見 <https://github.com/libffi/libffi/blob/master/LICENSE>。

## OpenSSL

- 授權：Apache-2.0，完整文字位於 `_internal/third_party_licenses/openssl/Apache-2.0.txt`。
- `libcrypto-3.dll`／`libssl-3.dll` 與 `-x64` 版本可能不同，盤點會分別記錄，不將它們描述為單一 runtime 版本。
- 原始碼與授權：<https://github.com/openssl/openssl>、<https://openssl-library.org/source/license/index.html>。

## Qt、PySide6 Essentials 與 Shiboken6

- 社群授權選項為 LGPL-3.0-only，並納入 GPL-3.0 條款；完整文字位於 `_internal/third_party_licenses/qt/LGPL-3.0.txt` 與 `GPL-3.0.txt`。Desk Sentry 不授予商業授權。
- 授權：<https://doc.qt.io/qtforpython-6/licenses.html>。
- 原始碼：<https://code.qt.io/cgit/pyside/pyside-setup.git/>、<https://download.qt.io/official_releases/QtForPython/pyside6/>；版本以本檔表格為準。
- Qt 第三方聲明：<https://doc.qt.io/qt-6/licenses-used-in-qt.html>。
- 共享函式庫應保持可由相容的修改版替換；依 LGPL 條款，允許為除錯修改版而進行 reverse engineering。安裝程式不限制擷取或替換這些函式庫。
- 本建置排除軟體 OpenGL fallback `opengl32sw.dll` 與 OpenCV FFmpeg 影片讀取元件；本聲明不表示它們隨程式散布。

## NumPy、OpenBLAS 與數值函式庫

- NumPy 使用 BSD-3-Clause，wheel metadata 另列出內含授權：`BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0`。
- 保留的 OpenBLAS DLL 包含 LAPACK（BSD-3-Clause-Open-MPI）與 GCC runtime 部分（GPL-3.0-or-later WITH GCC-exception-3.1）。
- wheel 彙總授權與各元件授權文字位於 `_internal/third_party_licenses/numpy/`，包含 `LICENSE.txt`。
- 原始碼：<https://github.com/numpy/numpy>、<https://github.com/OpenMathLib/OpenBLAS>、<https://github.com/Reference-LAPACK/lapack>、<https://gcc.gnu.org/git/>。
- 授權參考：<https://numpy.org/doc/stable/license.html>。

## opencv-python-headless 與 OpenCV

- wheel wrapper 使用 MIT；OpenCV 使用 Apache-2.0，並包含其他第三方條款。
- wheel 的 `LICENSE.txt` 與 `LICENSE-3RD-PARTY.txt` 會保留在 `_internal/third_party_licenses/opencv/`；再散布時必須保留兩者。
- 原始碼：<https://github.com/opencv/opencv-python>、<https://github.com/opencv/opencv>、<https://github.com/opencv/opencv_3rdparty>。

## Microsoft Visual C++ runtime 與 Universal CRT

- 保留的 `MSVCP140*`、`VCRUNTIME140*`、`ucrtbase.dll` 與 API-set DLL 是 Microsoft proprietary redistributables，不適用 MIT。各資源版本列於產生的表格與 inventory。
- 條款與可散布程式碼清單：<https://learn.microsoft.com/en-us/visualstudio/releases/2022/redistribution>、<https://visualstudio.microsoft.com/license-terms/>。
- 散布者必須自行確認其資格與適用條款；本盤點不授予 Microsoft 或 Qt 商業授權。

## 固定版本模型

| 模型／export | 授權與保留的完整文字 | 原始碼 |
| --- | --- | --- |
| NanoDet November 2022 | Apache-2.0；`licenses/NanoDet-OpenCV-Zoo-Apache-2.0.txt` | <https://huggingface.co/opencv/object_detection_nanodet/tree/5bfd47077350a726ad440dd7bd1e1e35e8ebcfb2> |
| YuNet May 2026 | MIT；`licenses/YuNet-OpenCV-Zoo-MIT.txt` | <https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet> |
| SFace December 2021 | Apache-2.0；`licenses/SFace-OpenCV-Zoo-Apache-2.0.txt` | <https://huggingface.co/opencv/face_recognition_sface/tree/3d7082438a6e4551e840c9b2bb60b71e8da4b524> |

產生的 inventory 會記錄 `tools/fetch_model.py` 驗證的三個模型 SHA-256。Desk Sentry 的 MIT 授權不取代模型各自的條款。

## 建置工具

PyInstaller 使用 GPL-2.0-or-later WITH Bootloader-exception，詳見 <https://pyinstaller.org/en/stable/license.html>；它是建置來源，不是應用程式 import。Inno Setup 授權位於 <https://jrsoftware.org/files/is/license.txt>。

## 專案圖示

應用程式圖示由本專案的 `tools/make_icon.py` 以程式產生，未使用第三方圖像素材，並與 Desk Sentry 原始碼一同依 MIT License 授權。
