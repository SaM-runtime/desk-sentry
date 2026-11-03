# Changelog

本檔依 [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) 整理使用者可見的變更，
版本遵循 [Semantic Versioning](https://semver.org/spec/v2.0.0.html)。
2.0.0 整理目前可供發佈的功能與公開文件；實際成品與驗證狀態以該版本 Release 為準。

## [2.0.0] - 2026-10-20

### Added

- 攝影機後照鏡、自己座位校準、追蹤、附近人物與接近警示，以及可分別關閉的鏡子、閃光與聲音。
- 小鏡子模式、快捷鍵、記憶體內最近提醒縮圖，以及選用的壓縮數字診斷記錄。
- 標記認得的人與警戒程度；臉部特徵以目前 Windows 帳號加密保存在本機。
- 縮小指定程式、離座動作、黑幕、鎖定或監看時段自動暫停，以及每日摘要。
- 低調模式、分級預警、掩護視窗、誤報／真實標記快捷鍵與鏡頭健康提示。
- 固定使用者安裝位置與可選的 main 分支 commit 後背景部署。
- 安裝／解除安裝與可攜版、唯一版本來源與建置資訊、設定 schema 遷移，以及正常退出 IPC。
- 預設關閉的 GitHub 更新檢查、每日節流、系統匣提示與略過版本。
- Windows CI 設定、tag 驗證，以及產生 installer、portable zip 與 `SHA256SUMS.txt` 的 draft release 流程。

### Changed

- 重寫應用程式架構；偵測改用 NanoDet-Plus，影像只在記憶體處理，不錄影、不存檔。
- 合併重複提醒並平滑顯示狀態；低調模式保留偵測與動作並隱藏影像。
- 開發部署和 installer 共用 `%LOCALAPPDATA%\Programs\DeskSentry` 與同一個 HKCU Run key。

### Fixed

- 修正 UI 控件滾輪誤改值、動畫物件生命週期、紀錄捲動與視窗邊界問題。
- 修正鏡頭中斷恢復後的自動動作確認、Windows 視窗還原與快捷鍵失敗處理。
- 部署正常結束引擎並 flush diagnostics，逾時才使用強制結束備援。

上一代 v1 原始碼已從公開工作樹移除；如需追溯舊版，請使用維護者保存的歷史備份。
