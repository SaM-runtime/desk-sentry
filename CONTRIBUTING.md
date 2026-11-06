# 貢獻指南

感謝你協助改善 Desk Sentry。專案支援 Windows 與 Python 3.12；送出變更前，請確認沒有加入相機影像、真實診斷資料、帳號憑證或其他個人資料。

## 開發環境

在 PowerShell 建立虛擬環境並安裝專案與開發工具：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

模型不納入 Git。第一次執行測試或建立發佈 bundle 前，請下載並驗證固定版本的模型：

```powershell
.\.venv\Scripts\python.exe tools\fetch_model.py
```

`fetch_model.py` 會對每個模型驗證 SHA-256；驗證失敗時不會把檔案當成可用模型。

## 驗證

送出 pull request 前請執行：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src tests tools packaging
.\.venv\Scripts\python.exe -m ruff format --check src tests tools packaging
.\.venv\Scripts\python.exe -m mypy src
.\.venv\Scripts\python.exe tools\privacy_scan.py --worktree
```

請為行為變更加上聚焦測試，並避免在測試中連到真實網路或讀寫使用者的 `%APPDATA%\DeskSentry`。
新增範例、測試資料或圖片前，請確認其中沒有姓名、帳號、電子郵件、本機絕對路徑、內網位址、公司資料、相機畫面或人臉。

## 開發版自動部署

專案提供 `tools/deploy.ps1`，可將本機建置安裝到 Desk Sentry 的固定使用者安裝位置。只需測試流程時，請指定暫存 `-InstallDir`，並搭配 `-SkipRegistry -NoStart`。

版本控管的 post-commit hook 採明確加入制。clone 後預設不啟用；需要在 `main` commit 後背景部署時，請自行執行：

```powershell
git config core.hooksPath tools/git-hooks
```

不需要此行為時，請勿啟用 hook。

