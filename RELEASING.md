# 發佈 Desk Sentry

此文件是維護者操作流程。日常 commit 後的本機部署與 GitHub 對外發佈分開執行；
建立 repository、設定 remote、commit、tag、push 與公開 release 都由維護者明確執行。

目前版本的唯一來源為 `src/desk_sentry/__init__.py` 的 `__version__`，
setuptools metadata、bundle 建置資訊及安裝程式都由它衍生。目前版本為 `2.0.1`。
正式版顯示純版本，未正式發佈的本機建置顯示 `版本+短commit[.dirty]`。

## 發佈前

1. 執行 `tools/privacy_scan.py --worktree`，確認預計加入版本控制的檔案不含私人資料、本機路徑、內部紀錄或真實影像；同時檢查第三方授權與模型來源。
2. 確認一般／低調模式、更新關閉時不連網、安裝／覆蓋升級／正常關閉／解除安裝保留設定的人工驗收結果。程式碼簽章尚未納入本專案流程；所有成品都必須提供 SHA-256 校驗檔。
3. 準備 [CONTRIBUTING.md](CONTRIBUTING.md) 的 Python 3.12 開發環境與三個 SHA-256 驗證模型；bundle 會下載並驗證寫死版本與雜湊的 python.org 官方嵌入式 Python。本機編譯 installer 另需已安裝的 Inno Setup 6。
4. repository 只在 `src/desk_sentry/project.py` 設定。README 的下載連結是受管理區塊，installer／workflow 也讀取同一來源；更換 repository 後同步產生連結，並檢查 repo 權限。

## 改版、驗證與提交

1. 只修改 `__version__`，使用 `MAJOR.MINOR.PATCH` 正式版本格式；在 CHANGELOG 加入同版本標題與日期，描述使用者可見變更。
2. 執行完整測試與靜態檢查，再建立 bundle，檢查官方 Python、`_pth`、build info、相依套件、模型與 installer。

```powershell
.\.venv\Scripts\python.exe packaging/build_release.py sync-readme --path README.md
.\.venv\Scripts\python.exe packaging/build_release.py check-readme --path README.md
.\.venv\Scripts\python.exe tools/privacy_scan.py --worktree
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src tests tools packaging
.\.venv\Scripts\python.exe -m ruff format --check src tests tools packaging
.\.venv\Scripts\python.exe -m mypy src
.\.venv\Scripts\python.exe packaging/build_release.py prepare --output .tmp/release
.\.venv\Scripts\python.exe packaging/build_release.py bundle --output .tmp/release/dist/DeskSentry --metadata-dir .tmp/release
```

離線已有官方嵌入式 zip 時，可在最後一行加上 `--python-zip <本機路徑>`；此參數只略過 Python zip 下載。執行期 wheel 仍由 `pip install --target` 取得，因此需要網路或已備妥的本機 pip cache，而且仍會執行固定版本與二進位 wheel 限制。

建置時 `DESKSENTRY_RELEASE=1` 表示正式版本，其餘為開發版本。
固定安裝測試須使用測試目錄及略過自啟／啟動參數；執行部署仍可能通知舊 `dist/` 位置的程序，
請先確認沒有需要保留的執行個體。本機 Inno 編譯方式以 CI release workflow 的指令為準，
沒有 ISCC 時在 CI 編譯驗證，不要為測試自動安裝它。

3. 檢查精確 diff 與檔案清單後，由維護者 commit。repository 與 remote 由維護者設定；clone 的 hooks 不會自動啟用。
4. 從唯一版本來源取值，建立對應 tag，再 push commit 與該 tag：

```powershell
$releaseVersion = & .\.venv\Scripts\python.exe -c "from desk_sentry import __version__; print(__version__)"
git tag "v$releaseVersion"
git push origin HEAD
git push origin "v$releaseVersion"
```

上述是人工發佈指令範例，工具不會自動代替維護者執行。

## 檢查 draft 再發佈

`release.yml` 只在 `v*` tag 觸發。先比對 tag 與 `__version__`，不一致直接失敗；
通過測試後產生內含官方嵌入式 Python 的免打包目錄、Inno installer、可攜 zip 及 `SHA256SUMS.txt`，
只上傳到 **draft** release。Release 內文使用 CHANGELOG 的該版本段落。

1. 在 Actions 確認所有步驟成功，開啟 draft，核對 tag、版本、變更內容與三個附件。
2. 下載 installer 和 zip，核對 checksum；在一般 Windows 使用者環境實測新裝、覆蓋升級、自啟與解除安裝。
3. 確認 README 的未簽章／SmartScreen 說明、隱私掃描與授權條件皆已處理後，才手動 Publish release。
4. 可選更新檢查只會看公開且非 draft／prerelease 的 latest release，draft 不會通知一般使用者。

若建置失敗，先修正原始碼／環境並重新驗證。不要讓版本不符的 tag 通過檢查；
已公開的版本應以新版本修正，是否撤回或重建尚未公開的 draft 由維護者決定。
