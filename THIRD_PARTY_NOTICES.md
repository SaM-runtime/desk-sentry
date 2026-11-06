# Desk Sentry 第三方聲明

本檔是建置成品的第三方元件盤點，不構成法律意見。發佈成品使用 python.org 官方 Windows 嵌入式 Python，並以固定版本的二進位 wheel 安裝執行期相依套件。下表版本取自建置時固定的 Python 版本與專案相依設定；wheel 自帶的 metadata 與授權檔一併保留在 `python/Lib/site-packages/`。

| 固定元件 | 發佈版本 |
| --- | --- |
| CPython | 3.12.10 |
| PySide6-Essentials | 6.11.1 |
| shiboken6 | 6.11.1 |
| NumPy | 2.5.1 |
| opencv-python-headless | 5.0.0.93 |

## CPython 與內含函式庫

- 授權：PSF License Version 2 與 Python 歷史聲明。官方嵌入式套件的完整文字保留為 `<bundle>/licenses/Python-PSF-License.txt`。
- 原始碼與授權：<https://github.com/python/cpython>、<https://docs.python.org/3.12/license.html>。
- 保留的 CPython extension 包含 libffi（MIT）、Expat（MIT）、bzip2（BSD-style）、liblzma（0BSD／public domain）與 mpdecimal（BSD-2-Clause）。Python 授權檔內含相關文字與來源；libffi 授權見 <https://github.com/libffi/libffi/blob/master/LICENSE>。

## OpenSSL

- 授權：Apache-2.0，完整文字位於 `<bundle>/licenses/runtime/OpenSSL-Apache-2.0.txt`。
- 原始碼與授權：<https://github.com/openssl/openssl>、<https://openssl-library.org/source/license/index.html>。

## Qt、PySide6 Essentials 與 Shiboken6

- 社群授權選項為 LGPL-3.0-only，並納入 GPL-3.0 條款；完整文字位於 `<bundle>/licenses/runtime/Qt-LGPL-3.0.txt` 與 `<bundle>/licenses/runtime/Qt-GPL-3.0.txt`。Desk Sentry 不授予商業授權。
- 授權：<https://doc.qt.io/qtforpython-6/licenses.html>。
- 原始碼：<https://code.qt.io/cgit/pyside/pyside-setup.git/>、<https://download.qt.io/official_releases/QtForPython/pyside6/>；版本以本檔表格為準。
- Qt 第三方聲明：<https://doc.qt.io/qt-6/licenses-used-in-qt.html>。
- 共享函式庫應保持可由相容的修改版替換；依 LGPL 條款，允許為除錯修改版而進行 reverse engineering。安裝程式不限制擷取或替換這些函式庫。

## NumPy、OpenBLAS 與數值函式庫

- NumPy 使用 BSD-3-Clause，wheel metadata 另列出內含授權：`BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0`。
- 保留的 OpenBLAS DLL 包含 LAPACK（BSD-3-Clause-Open-MPI）與 GCC runtime 部分（GPL-3.0-or-later WITH GCC-exception-3.1）。
- wheel 彙總授權與各元件授權文字保留在 `python/Lib/site-packages/` 的 NumPy metadata 與套件目錄中。
- 原始碼：<https://github.com/numpy/numpy>、<https://github.com/OpenMathLib/OpenBLAS>、<https://github.com/Reference-LAPACK/lapack>、<https://gcc.gnu.org/git/>。
- 授權參考：<https://numpy.org/doc/stable/license.html>。

## opencv-python-headless 與 OpenCV

- wheel wrapper 使用 MIT；OpenCV 使用 Apache-2.0，並包含其他第三方條款。
- wheel 的 `LICENSE.txt` 與 `LICENSE-3RD-PARTY.txt` 會保留在 `python/Lib/site-packages/` 的 OpenCV metadata 中；再散布時必須保留兩者。
- 原始碼：<https://github.com/opencv/opencv-python>、<https://github.com/opencv/opencv>、<https://github.com/opencv/opencv_3rdparty>。

## Microsoft Visual C++ runtime 與 Universal CRT

- 官方 Python 與二進位 wheel 可能包含 `MSVCP140*`、`VCRUNTIME140*`、`ucrtbase.dll` 或 API-set DLL；這些 Microsoft proprietary redistributables 不適用 MIT。實際檔案與版本須以完成的 bundle 為準。
- 條款與可散布程式碼清單：<https://learn.microsoft.com/en-us/visualstudio/releases/2022/redistribution>、<https://visualstudio.microsoft.com/license-terms/>。
- 散布者必須自行確認其資格與適用條款；本盤點不授予 Microsoft 或 Qt 商業授權。

## 固定版本模型

| 模型／export | 授權與保留的完整文字 | 原始碼 |
| --- | --- | --- |
| NanoDet November 2022 | Apache-2.0；`licenses/NanoDet-OpenCV-Zoo-Apache-2.0.txt` | <https://huggingface.co/opencv/object_detection_nanodet/tree/5bfd47077350a726ad440dd7bd1e1e35e8ebcfb2> |
| YuNet May 2026 | MIT；`licenses/YuNet-OpenCV-Zoo-MIT.txt` | <https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet> |
| SFace December 2021 | Apache-2.0；`licenses/SFace-OpenCV-Zoo-Apache-2.0.txt` | <https://huggingface.co/opencv/face_recognition_sface/tree/3d7082438a6e4551e840c9b2bb60b71e8da4b524> |

`tools/fetch_model.py` 會驗證三個模型的 SHA-256。Desk Sentry 的 MIT 授權不取代模型各自的條款。

## 建置工具

Inno Setup 授權位於 <https://jrsoftware.org/files/is/license.txt>；它只用於建立安裝程式，不是應用程式執行期相依套件。

## 專案圖示

應用程式圖示由本專案的 `tools/make_icon.py` 以程式產生，未使用第三方圖像素材，並與 Desk Sentry 原始碼一同依 MIT License 授權。
