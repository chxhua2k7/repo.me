# chxhua2k7's Repo

<https://repo.y00e.org> 的原始碼。這是一個越獄插件源，Sileo、Zebra、Saily、Cydia 都能加入。

整個網站由 [`tools/repo.py`](tools/repo.py) 產生。它會抓下列 GitHub repo 所有 release 裡的 `.deb`，再加上 `debs/` 裡手動放的檔案，產生以下內容：

- APT 索引：`Packages`（含 gz / bz2 / xz / zst）和 `Release`
- 每個套件的 Sileo 原生 depiction（`depiction.json`，分「詳細資訊」和「更新紀錄」兩頁）
- 給 Cydia、Zebra 和瀏覽器看的網頁 depiction（會依使用者的 iOS 版本顯示相容性）
- 首頁（套件列表、最近更新）和 `sileo-featured.json`

產物只會部署到 GitHub Pages，不會 commit 回這個 repo。

## 自動更新

[`.github/workflows/build.yml`](.github/workflows/build.yml) 在下面這些時候重建並部署：

| 時機 | 說明 |
| --- | --- |
| 插件 repo 發 release | 插件 repo 的 workflow 送出 `release-sync` 事件，大約一分鐘就上架 |
| push 到 `main` | 改了 `repo.toml`、`meta/`、`debs/`、`site/` 或產生器 |
| 每 6 小時 | 備援。插件 repo 的 README 或 release 說明改了，也會在這時跟上 |
| 手動 | Actions › Build & deploy repo › Run workflow |

插件 repo 發完 release 後，要送出這個事件：

```yaml
- name: Notify repo.me
  if: github.ref == 'refs/heads/main'
  env:
    GH_TOKEN: ${{ secrets.REPO_ME_DISPATCH_TOKEN }}
  run: |
    gh api repos/chxhua2k7/repo.me/dispatches \
      -f event_type=release-sync \
      -f "client_payload[repo]=${{ github.repository }}"
```

`REPO_ME_DISPATCH_TOKEN` 是一個對 `chxhua2k7/repo.me` 有 Contents 寫入權限的 token。Gravitation、MetalHudLoader、TapTapTipTapTime 都已經設好了。

## 資料夾

| 路徑 | 用途 |
| --- | --- |
| `repo.toml` | 全域設定、自訂變數、底部連結、更新紀錄規則、套件來源 |
| `meta/<套件 ID>/` | 單一套件的額外設定（全部可省略，見下方） |
| `debs/` | 手動放的 `.deb`。發布日期取它第一次被 commit 的時間 |
| `site/` | 原樣複製到網站根目錄的檔案：repo 圖示、`CNAME`、CSS、JS |
| `tools/repo.py` | 產生器 |

## 新增插件

- 有 GitHub release 的：在 `repo.toml` 加一段 `[[source]]`，然後在那個 repo 加上面的通知步驟。
- 只有 `.deb` 的：放進 `debs/`，push。

```toml
[[source]]
github = "chxhua2k7/NewTweak"
# prereleases = true      連 pre-release 也上架
# assets = "*.deb"        只收符合的檔名
# changelog = "auto"      "auto"、"release"、"commits"、"none"
# readme = false          不用 README 當介紹
```

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
| 日期 | GitHub release 的發布時間；`debs/` 裡的是第一次 commit 的時間 |

## 更新紀錄

每個版本的說明依序取自：

1. `meta/<套件>/changelog.md` 裡同版本的段落
2. GitHub release 的說明（自動去掉「Full Changelog」那行）
3. 上一個 release 到這個 release 之間的 commit：
   - 略過合併 commit、只改 CI 或文件的 commit（`[changelog].ignore_paths`），和訊息符合 `[changelog].ignore` 的 commit
   - 訊息開頭的「1.4.1: 」這類版本號會拿掉
   - commit 內文裡的 `- 項目` 會列成子項目
   - 同一個 commit 如果在舊版本已經出現過，就不再重複列出
4. 都沒有的話：最舊的版本寫「Initial release.」，其他寫「No release notes.」

`changelog.md` 的格式（日期可省略，有寫就會覆蓋 release 日期）：

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
| `{{source_repo}}`、`{{source_url}}`、`{{releases_url}}`、`{{issues_url}}` | 來源 GitHub repo |
| `{{release_url}}`、`{{release_tag}}` | 最新版的 GitHub release |
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

到這個 repo 的 Settings › Secrets › Actions 加一個 `GPG_SIGNING_KEY`，內容是 `gpg --armor --export-secret-keys <key>` 的輸出。之後每次建置都會多產生 `Release.gpg` 和 `InRelease`。
