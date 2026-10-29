# chxhua2k7's Repo

<https://repo.y00e.org> 的原始碼。這是一個越獄插件源，Sileo、Zebra、Saily、Cydia 都能加入。

## 發新版本

1. 改插件 repo 裡 `control` 的 `Version`（要比上一版大）
2. push 到預設分支

就這樣。15 分鐘內，這裡的 workflow 會看到新版本號，從原始碼編出 `.deb`、放進 `debs/`，然後更新 repo。插件 repo 不用發 release，也不用放任何 workflow 或 token。在其他分支 push 不會有任何影響。

想馬上上架，不想等：

```bash
gh workflow run build.yml --repo chxhua2k7/repo.me
```

## 新增插件

插件 repo 要是公開的，根目錄要有 Theos 的 `Makefile` 和 `control`；`Makefile` 的 `TARGET` 要寫明 SDK 版本，例如 `iphone:clang:16.5:15.0`。然後在 `repo.toml` 加兩行，push：

```toml
[[source]]
github = "chxhua2k7/NewTweak"
```

`[[source]]` 的可選設定：

```toml
# make = "package FINALPACKAGE=1"   給 make 的參數
# sdk = "16.5"                      Makefile 的 TARGET 沒寫 SDK 版本時用
# build = false                     不自動編，只用 debs/ 裡手動放的
# changelog = "none"                不從 commit 產生更新紀錄
# readme = false                    不用 README 當介紹
```

私有 repo、或不想讓這裡編的插件：自己編好 `.deb` 放進 `debs/`，push。

## 運作方式

[`.github/workflows/build.yml`](.github/workflows/build.yml) 每 15 分鐘、push 到 `main`、或手動執行時：

1. **check**：讀每個插件預設分支的 `control`，版本號在 `debs/` 裡還沒有的，列為要編
2. **tweak**：在 macOS 上用 Theos 編那些版本（只抓 Makefile 指定的 SDK）
3. **site**：把編好的 `.deb` commit 進 `debs/`、記到 `debs/builds.json`（哪個 repo、哪個 commit、什麼時候），再用 [`tools/repo.py`](tools/repo.py) 產生整個網站；跟線上一樣就不部署
4. **deploy**：部署到 GitHub Pages

同一個 commit 編失敗只會試一次，push 新的 commit 或手動執行才會再試，不會每 15 分鐘寄一封失敗通知。

產生的內容：

- APT 索引：`Packages`（含 gz / bz2 / xz / zst）和 `Release`
- 每個套件的 Sileo 原生 depiction（`depiction.json`，分「詳細資訊」和「更新紀錄」兩頁）
- 給 Cydia、Zebra 和瀏覽器看的網頁 depiction（會依使用者的 iOS 版本顯示相容性）
- 首頁（套件列表、最近更新）和 `sileo-featured.json`

這個 repo 的 `main` 會有 bot 的「Add …」commit，本機改東西前先 `git pull`。

## 資料夾

| 路徑 | 用途 |
| --- | --- |
| `repo.toml` | 全域設定、自訂變數、底部連結、更新紀錄規則、插件來源 |
| `debs/` | 所有上架的 `.deb`，和記錄出處的 `builds.json` |
| `meta/<套件 ID>/` | 單一套件的額外設定（全部可省略，見下方） |
| `site/` | 原樣複製到網站根目錄的檔案：repo 圖示、`CNAME`、CSS、JS |
| `tools/repo.py` | 產生器 |

## 會自動抓的東西

沒在 `meta/` 寫的欄位，會依序從下面的來源拿：

| 項目 | 來源（前面的優先） |
| --- | --- |
| 名稱 | `name` → `.deb` 的 `Name` → 套件 ID |
| 一句話介紹 | `tagline` → `.deb` 的 `Description`（如果是 Theos 預設的「An awesome … tweak!」就跳過）→ GitHub repo 描述 |
| 介紹 | `description` → `description.md` → README（去掉標題、建置、安裝、專案結構、授權等段落；開頭和一句話介紹重複的段落也拿掉） |
| 截圖 | `screenshots` → `screenshots/` 資料夾 → README 裡的圖片。超過 400 KB 會轉成 JPEG |
| 圖示 | `icon` → `icon.png` → `.deb` 裡設定 bundle 的 `icon@3x.png`、控制中心模組的 `SettingsIcon@3x.png` |
| 橫幅 | `header` → `header.png` → `.deb` 裡設定 bundle 的 `header.png` |
| 相容性 | `min_ios` / `max_ios` → `Depends` 裡的 `firmware (>= …)` / `firmware (<< …)` |
| 越獄類型 | 架構：`iphoneos-arm` = Rootful，`iphoneos-arm64` = Rootless，`iphoneos-arm64e` = Roothide |
| 連結 | 原始碼、原始專案（fork 的話）、回報問題（repo 有開 Issues 的話）、`homepage`、`[[link]]` |
| 日期 | `debs/builds.json` 記的上架時間；手動放的 `.deb` 是第一次 commit 的時間 |

## 更新紀錄

每個版本的說明依序取自：

1. `meta/<套件>/changelog.md` 裡同版本的段落
2. 上一版到這一版之間的 commit（`debs/builds.json` 記著每版是從哪個 commit 編的）：
   - 略過合併 commit、只改 CI 或文件的 commit（`[changelog].ignore_paths`），和訊息符合 `[changelog].ignore` 的 commit
   - 訊息開頭的「1.4.1: 」這類版本號會拿掉
   - commit 內文裡的 `- 項目` 會列成子項目
   - 同一個 commit 如果在舊版本已經出現過，就不再重複列出
3. 都沒有的話：最舊的版本寫「Initial release.」，其他寫「No release notes.」

`changelog.md` 的格式（日期可省略，有寫就會覆蓋上架日期）：

```markdown
## 1.2.0 (2026-10-01)
- New thing
- Fixed {{name}} crashing on iOS 16

## 1.1.0
- …
```

`changelog.md` 裡可以寫 `.deb` 已經不存在的舊版本，這些版本一樣會顯示在更新紀錄裡。

## `meta/<套件>/package.toml`

用 `python3 tools/repo.py new <套件 ID>` 可以產生一份範本，裡面每一項都有註解。

| 欄位 | 說明 |
| --- | --- |
| `name`、`tagline`、`author`、`maintainer`、`section` | 覆寫顯示資訊（`author`、`maintainer` 也會寫進 `Packages`） |
| `description` | 長介紹（Markdown），也可以改寫在 `description.md` |
| `tint` | 主題色，例如 `"#FF9500"` |
| `min_ios`、`max_ios` | 相容範圍。`max_ios = "17"` 代表整個 17.x 都可以 |
| `homepage` | 首頁網址 |
| `tags` | 加進 `Tag` 欄位 |
| `icon`、`header`、`banner`、`screenshots` | 圖片路徑（相對這個資料夾）或網址 |
| `featured` | `false` 就不放進 Sileo 精選 |
| `hidden` | `true` 就整個套件不上架 |
| `hide_versions` | 不上架的版本，例如 `["1.0"]` |
| `keep_versions` | 只保留最新的幾個版本 |
| `[vars]` | 這個套件專用的自訂變數 |
| `[control]` | 直接寫進 `Packages` 的欄位，會覆蓋 `.deb` 裡的值；值寫 `""` 代表刪掉該欄位 |
| `[[info]]` | 在資訊表多加一列：`title`、`text` |
| `[[link]]` | 在連結區多加一個按鈕：`name`、`url` |

## 變數

`repo.toml` 和 `meta/` 裡的文字欄位（介紹、更新紀錄、連結、資訊表、`[control]`、`[vars]`）都可以寫 `{{變數}}`。
如果寫了不存在的變數，建置時會出現警告，原字串保持不變。

`python3 tools/repo.py vars <套件 ID>` 會列出所有變數和目前的值。

**全站**

| 變數 | 內容 |
| --- | --- |
| `{{repo_name}}`、`{{repo_url}}`、`{{repo_description}}`、`{{repo_icon_url}}` | `repo.toml` 的 `[repo]` |
| `repo.toml` 的 `[vars]` | 自訂（現在有 `{{github}}`） |

**套件**

| 變數 | 例子 |
| --- | --- |
| `{{package}}`、`{{name}}`、`{{tagline}}` | `com.yulkytulky.taptaptiptaptime`、`TapTapTipTapTime`、… |
| `{{author}}`、`{{maintainer}}`、`{{section}}` | `YulkyTulky`、`YulkyTulky`、`Tweaks` |
| `{{version}}`、`{{architecture}}`、`{{jailbreak}}` | `27.1`、`iphoneos-arm64`、`Rootless` |
| `{{min_ios}}`、`{{max_ios}}`、`{{compatibility}}` | `15.0`、（空）、`iOS 15.0+` |
| `{{depends}}` | `mobilesubstrate, preferenceloader` |
| `{{size}}`、`{{installed_size}}` | `213.2 KB`、`588.0 KB` |
| `{{updated}}`、`{{released}}`、`{{version_count}}` | 最新版日期、第一版日期、版本數 |
| `{{depiction_url}}`、`{{sileo_depiction_url}}` | 網頁和 Sileo depiction 網址 |
| `{{icon_url}}`、`{{header_url}}`、`{{download_url}}` | 圖示、橫幅、最新 `.deb` 的網址 |
| `{{sileo_url}}`、`{{zebra_url}}` | `sileo://package/…`、`zbra://packages/…` |
| `{{homepage}}` | 首頁 |
| `{{source_repo}}`、`{{source_url}}`、`{{issues_url}}` | 來源 GitHub repo |
| `{{commit}}`、`{{commit_url}}` | 最新版是從哪個 commit 編的 |
| `{{upstream_repo}}`、`{{upstream_url}}` | fork 的原始專案 |
| `package.toml` 的 `[vars]` | 自訂（可以覆蓋上面的變數） |

## 本機建置

需要 Python 3.11 以上（macOS 請用 Homebrew 的 `python3`）。截圖縮圖會用 Pillow，沒有的話 macOS 會改用內建的 `sips`。

```bash
python3 tools/repo.py build
```

```bash
python3 -m http.server 8000 --directory _site
```

- `--offline`：不連網，只用 `.cache/` 裡上次抓到的資料
- token：有設 `GH_TOKEN` 就用它；沒有的話會用 `gh auth token`，兩者都沒有就用未登入的 API 額度

## 簽署 Release（可選）

到這個 repo 的 Settings › Secrets › Actions 加一個 `GPG_SIGNING_KEY`，內容是 `gpg --armor --export-secret-keys <key>` 的輸出。之後每次部署都會多產生 `Release.gpg` 和 `InRelease`。
