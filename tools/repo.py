#!/usr/bin/env python3
"""repo.y00e.org 產生器。

debs/ 裡的 .deb（workflow 從 repo.toml 列出的插件原始碼編好放進來的，或手動放的）
加上各插件 GitHub repo 的 README 與 commit，產生 APT 索引（Packages、Release）、Sileo 原生 depiction、網頁 depiction、首頁和
sileo-featured.json，輸出到 _site/ 給 GitHub Pages 部署。

  python3 tools/repo.py build             產生 _site/
  python3 tools/repo.py build --offline   不連網，只用 .cache/ 裡上次抓到的資料
  python3 tools/repo.py vars [套件]       列出可用的 {{變數}} 和目前的值
  python3 tools/repo.py new <套件>        建立 meta/<套件>/package.toml 範本
  python3 tools/repo.py pending           （workflow 用）列出要編的新版本
  python3 tools/repo.py record <資料夾>   （workflow 用）把編好的 .deb 加進 debs/
"""

from __future__ import annotations

import sys

if sys.version_info < (3, 11):
    sys.exit("需要 Python 3.11 以上（用到 tomllib）。macOS 請用 Homebrew 的 python3。")

import argparse
import base64
import bz2
import datetime as dt
import email.utils
import fnmatch
import functools
import gzip
import hashlib
import html
import io
import json
import lzma
import os
import re
import shutil
import struct
import subprocess
import tarfile
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / ".cache"
UTC = dt.timezone.utc

# 來源 repo 第一個編出來的版本的更新紀錄；組套件時才知道它是不是最舊的版本
INITIAL = "\x00initial"

STRINGS = {
    "en": {
        "details": "Details",
        "changelog": "Changelog",
        "information": "Information",
        "version": "Version",
        "updated": "Updated",
        "size": "Size",
        "compatibility": "Compatibility",
        "jailbreak": "Jailbreak",
        "author": "Author",
        "section": "Section",
        "requires": "Requires",
        "links": "Links",
        "source_code": "Source Code",
        "report_issue": "Report an Issue",
        "homepage": "Homepage",
        "based_on": "Based on {repo}",
        "screenshot": "Screenshot {n}",
        "initial_release": "Initial release.",
        "no_notes": "No release notes.",
        "packages": "Packages",
        "recent_updates": "Recent Updates",
        "add_to": "Add to {manager}",
        "copy_url": "Copy",
        "copied": "Copied",
        "open_in_sileo": "Open in Sileo",
        "download_deb": "Download .deb",
        "ok": "Compatible with your iOS {ios}.",
        "too_old": "Requires iOS {min} or later. You're on iOS {ios}.",
        "too_new": "Supports up to iOS {max}. You're on iOS {ios}.",
        "ios_min": "iOS {min}+",
        "ios_max": "Up to iOS {max}",
        "ios_range": "iOS {min} – {max}",
        "versions": "{n} versions",
        "no_packages": "No packages yet.",
    },
    "zh-Hant": {
        "details": "詳細資訊",
        "changelog": "更新紀錄",
        "information": "資訊",
        "version": "版本",
        "updated": "更新日期",
        "size": "大小",
        "compatibility": "相容性",
        "jailbreak": "越獄類型",
        "author": "作者",
        "section": "分類",
        "requires": "相依套件",
        "links": "連結",
        "source_code": "原始碼",
        "report_issue": "回報問題",
        "homepage": "首頁",
        "based_on": "原始專案 {repo}",
        "screenshot": "截圖 {n}",
        "initial_release": "首次發布。",
        "no_notes": "沒有更新說明。",
        "packages": "套件",
        "recent_updates": "最近更新",
        "add_to": "加入 {manager}",
        "copy_url": "複製",
        "copied": "已複製",
        "open_in_sileo": "在 Sileo 開啟",
        "download_deb": "下載 .deb",
        "ok": "相容你的 iOS {ios}。",
        "too_old": "需要 iOS {min} 以上，你的是 iOS {ios}。",
        "too_new": "只支援到 iOS {max}，你的是 iOS {ios}。",
        "ios_min": "iOS {min} 以上",
        "ios_max": "iOS {max} 以下",
        "ios_range": "iOS {min} – {max}",
        "versions": "{n} 個版本",
        "no_packages": "還沒有套件。",
    },
}
T = STRINGS["en"]

JAILBREAKS = {"iphoneos-arm": "Rootful", "iphoneos-arm64": "Rootless", "iphoneos-arm64e": "Roothide"}

# Theos 範本的預設描述，碰到就改用 GitHub repo 的描述
PLACEHOLDER_DESCRIPTION = re.compile(r"(?i)^an awesome .*(tweak|application|tool)!?$")

# 沒指定圖片時，到 .deb 裡找這些檔案
ICON_PATTERNS = [
    "*Library/PreferenceBundles/*.bundle/icon@3x.png",
    "*Library/ControlCenter/Bundles/*.bundle/SettingsIcon@3x.png",
    "*Applications/*.app/AppIcon60x60@3x.png",
    "*Library/PreferenceBundles/*.bundle/icon@2x.png",
    "*Library/PreferenceBundles/*.bundle/icon.png",
]
HEADER_PATTERNS = [
    "*Library/PreferenceBundles/*.bundle/header.png",
    "*Library/PreferenceBundles/*.bundle/banner.png",
]

# README 裡這些段落不放進介紹
README_DROP = re.compile(
    r"(?i)\b(build|compil|install|structure|layout|develop|contribut|licen[cs]e|credit|thank|donat|support me"
    r"|table of contents|todo|roadmap|faq)"
)

DEFAULT_REPO = {
    "name": "My Repo",
    "url": "https://example.com",
    "description": "",
    "language": "en",
    "tint": "#5E5CE6",
    "icon": "CydiaIcon.png",
    "origin": "",
    "label": "",
    "codename": "",
    "suite": "stable",
    "version": "1.0",
    "architectures": ["iphoneos-arm", "iphoneos-arm64"],
}

DEFAULT_CHANGELOG = {
    "ignore": [
        r"^Merge ",
        r"^(ci|chore|build|docs|test|style)(\(.*\))?!?:",
        r"(?i)\.DS_Store",
        r"(?i)^bump version",
        r"(?i)^version bump",
        r"(?i)^(add|update|edit) readme",
    ],
    "ignore_paths": [".github/*", "*.md", ".gitignore", "*.DS_Store", "LICENSE*", "images/*", "screenshots/*"],
    "commit_bodies": True,
}

ADD_LINKS = [
    ("Sileo", "sileo://source/{url}/"),
    ("Zebra", "zbra://sources/add/{url}/"),
    ("Saily", "apt-repo://{url}/"),
    ("Cydia", "cydia://url/https://cydia.saurik.com/api/share#?source={url}/"),
]


def info(msg: str) -> None:
    print(msg, file=sys.stderr)


def warn(msg: str) -> None:
    print(("::warning::" if os.environ.get("GITHUB_ACTIONS") else "warning: ") + msg, file=sys.stderr)


def esc(value) -> str:
    return html.escape(str(value), quote=True)


def load_toml(path: Path) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def parse_time(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def day(when: dt.datetime | None) -> str:
    return when.strftime("%Y-%m-%d") if when else ""


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


# ---------------------------------------------------------------- dpkg 版本比較


def _order(c: str) -> int:
    if c.isdigit():
        return 0
    if c.isalpha():
        return ord(c)
    if c == "~":
        return -1
    return ord(c) + 256


def _verrevcmp(a: str, b: str) -> int:
    i = j = 0
    while i < len(a) or j < len(b):
        while (i < len(a) and not a[i].isdigit()) or (j < len(b) and not b[j].isdigit()):
            ac = _order(a[i]) if i < len(a) else 0
            bc = _order(b[j]) if j < len(b) else 0
            if ac != bc:
                return ac - bc
            i += 1
            j += 1
        while i < len(a) and a[i] == "0":
            i += 1
        while j < len(b) and b[j] == "0":
            j += 1
        diff = 0
        while i < len(a) and a[i].isdigit() and j < len(b) and b[j].isdigit():
            diff = diff or ord(a[i]) - ord(b[j])
            i += 1
            j += 1
        if i < len(a) and a[i].isdigit():
            return 1
        if j < len(b) and b[j].isdigit():
            return -1
        if diff:
            return diff
    return 0


def _split_version(v: str) -> tuple[int, str, str]:
    epoch, _, rest = v.partition(":") if ":" in v else ("0", "", v)
    upstream, _, revision = rest.rpartition("-") if "-" in rest else (rest, "", "")
    return int(epoch) if epoch.isdigit() else 0, upstream, revision


def compare_versions(a: str, b: str) -> int:
    ea, ua, ra = _split_version(a)
    eb, ub, rb = _split_version(b)
    if ea != eb:
        return -1 if ea < eb else 1
    return _verrevcmp(ua, ub) or _verrevcmp(ra, rb)


version_key = functools.cmp_to_key(compare_versions)


# ---------------------------------------------------------------- GitHub


class GitHub:
    API = "https://api.github.com"

    def __init__(self, offline: bool):
        self.offline = offline
        self.token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or self._cli_token()

    @staticmethod
    def _cli_token() -> str | None:
        try:
            out = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            return None
        return out.stdout.strip() or None

    def _fetch(self, url: str, accept: str) -> bytes:
        req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "repo.y00e.org-builder"})
        if self.token and url.startswith(self.API):
            req.add_header("Authorization", f"Bearer {self.token}")
            req.add_header("X-GitHub-Api-Version", "2022-11-28")
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read()

    def get(self, path: str, immutable: bool = False):
        """GET api.github.com/<path>。回應存進 .cache/api/；immutable 的（commit）有快取就不再抓。"""
        cache = CACHE / "api" / (re.sub(r"[^\w.-]+", "_", path.strip("/")) + ".json")
        if self.offline or (immutable and cache.exists()):
            return json.loads(cache.read_text()) if cache.exists() else None
        try:
            data = json.loads(self._fetch(f"{self.API}/{path.lstrip('/')}", "application/vnd.github+json"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(data))
        return data

    def file(self, repo: str, path: str, ref: str) -> str | None:
        """repo 在某個 commit 的檔案內容。"""
        quote = urllib.parse.quote
        data = self.get(f"repos/{repo}/contents/{quote(path)}?ref={quote(ref, safe='')}", immutable=True)
        if not isinstance(data, dict) or not data.get("content"):
            return None
        return base64.b64decode(data["content"]).decode("utf-8", "replace")

    def download(self, url: str, dest: Path, size: int | None = None) -> Path:
        if dest.exists() and (size is None or dest.stat().st_size == size):
            return dest
        if self.offline:
            raise RuntimeError(f"--offline 但快取裡沒有 {dest.name}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        part.write_bytes(self._fetch(url, "application/octet-stream"))
        part.replace(dest)
        return dest


# ---------------------------------------------------------------- .deb


def _ar_members(data: bytes) -> dict[str, bytes]:
    if not data.startswith(b"!<arch>\n"):
        raise ValueError("not an ar archive")
    members, pos = {}, 8
    while pos + 60 <= len(data):
        header = data[pos:pos + 60]
        name = header[:16].decode().strip().rstrip("/")
        size = int(header[48:58].decode().strip())
        members[name] = data[pos + 60:pos + 60 + size]
        pos += 60 + size + (size & 1)
    return members


def _zstd_decompress(blob: bytes) -> bytes:
    try:
        from compression import zstd  # Python 3.14+
        return zstd.decompress(blob)
    except ImportError:
        return subprocess.run(["zstd", "-dcq"], input=blob, capture_output=True, check=True).stdout


def _open_tar(members: dict[str, bytes], prefix: str) -> tarfile.TarFile:
    for name, blob in members.items():
        if not name.startswith(prefix):
            continue
        ext = name[len(prefix):]
        if ext == ".gz":
            raw = gzip.decompress(blob)
        elif ext in (".xz", ".lzma"):
            raw = lzma.decompress(blob)
        elif ext == ".bz2":
            raw = bz2.decompress(blob)
        elif ext == ".zst":
            raw = _zstd_decompress(blob)
        elif ext == "":
            raw = blob
        else:
            raise ValueError(f"unsupported {name}")
        return tarfile.open(fileobj=io.BytesIO(raw))
    raise ValueError(f"{prefix}.* missing")


def _member_path(name: str) -> str:
    return re.sub(r"^\./", "", name).lstrip("/")


def parse_control(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    key = None
    for line in text.splitlines():
        if not line.strip():
            continue
        if line[0] in " \t" and key:
            fields[key] += "\n" + line
        elif ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            fields[key] = value.strip()
    return fields


def read_control(path: Path) -> dict[str, str]:
    tar = _open_tar(_ar_members(path.read_bytes()), "control.tar")
    for member in tar.getmembers():
        if _member_path(member.name) == "control":
            return parse_control(tar.extractfile(member).read().decode("utf-8", "replace"))
    raise ValueError(f"{path.name} has no control file")


def find_in_deb(path: Path, patterns: list[str]) -> bytes | None:
    tar = _open_tar(_ar_members(path.read_bytes()), "data.tar")
    files = {_member_path(m.name): m for m in tar.getmembers() if m.isfile()}
    for pattern in patterns:
        for name in sorted(files):
            if fnmatch.fnmatch(name, pattern):
                return tar.extractfile(files[name]).read()
    return None


def image_size(data: bytes) -> tuple[int, int] | None:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return struct.unpack(">II", data[16:24])
    if data[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker == 0xFF or marker == 0x01 or 0xD0 <= marker <= 0xD9:
                i += 1 if marker == 0xFF else 2
                continue
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                return w, h
            i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
    return None


@functools.cache
def _no_image_tool() -> None:
    warn("沒有 Pillow 也沒有 sips，截圖保持原檔（pip install pillow 可以縮小）")


def compact_screenshot(data: bytes, suffix: str) -> tuple[bytes, str]:
    """太大的截圖轉成高度最多 1334 的 JPEG。用 Pillow，沒有就用 macOS 的 sips。"""
    if len(data) <= 400_000 or not image_size(data):
        return data, suffix
    try:
        from PIL import Image as PILImage
    except ImportError:
        PILImage = None
    if PILImage:
        image = PILImage.open(io.BytesIO(data)).convert("RGB")
        image.thumbnail((4096, 1334))
        out = io.BytesIO()
        image.save(out, "JPEG", quality=85, optimize=True, progressive=True)
        return out.getvalue(), ".jpg"
    if shutil.which("sips"):
        work = CACHE / "sips"
        work.mkdir(parents=True, exist_ok=True)
        (work / f"in{suffix}").write_bytes(data)
        height = image_size(data)[1]
        resize = ["--resampleHeight", "1334"] if height > 1334 else []
        subprocess.run(["sips", "-s", "format", "jpeg", "-s", "formatOptions", "85", *resize,
                        str(work / f"in{suffix}"), "--out", str(work / "out.jpg")], capture_output=True, check=True)
        return (work / "out.jpg").read_bytes(), ".jpg"
    _no_image_tool()
    return data, suffix


# ---------------------------------------------------------------- 資料模型


@dataclass
class Source:
    repo: str
    conf: dict
    info: dict
    readme: str | None = None
    head: str | None = None          # 預設分支最新的 commit
    control: dict | None = None      # 那個 commit 的 control

    @property
    def url(self) -> str:
        return f"https://github.com/{self.repo}"

    @property
    def branch(self) -> str:
        return self.info.get("default_branch") or "main"


@dataclass
class Deb:
    path: Path
    control: dict
    date: dt.datetime
    source: Source | None = None
    commit: str | None = None       # 編出這個 .deb 的 commit（debs/builds.json）
    notes: str | None = None
    auto_notes: bool = False        # notes 是從 commit 產生的

    @property
    def package(self) -> str:
        return self.control.get("Package", "")

    @property
    def version(self) -> str:
        return self.control.get("Version", "")

    @property
    def arch(self) -> str:
        return self.control.get("Architecture", "")


@dataclass
class Image:
    name: str                       # depictions/<套件>/ 底下的檔名；外部網址時為空
    data: bytes | None = None
    url: str | None = None
    size: tuple[int, int] | None = None


@dataclass
class Entry:
    version: str
    date: dt.datetime | None
    notes: str | None
    auto: bool = False


class Config:
    def __init__(self, path: Path):
        raw = load_toml(path)
        self.repo = {**DEFAULT_REPO, **raw.get("repo", {})}
        self.repo["url"] = self.repo["url"].rstrip("/")
        for key in ("origin", "label", "codename"):
            self.repo[key] = self.repo[key] or self.repo["name"]
        self.vars = {k: str(v) for k, v in raw.get("vars", {}).items()}
        self.links = raw.get("link", [])
        self.changelog = {**DEFAULT_CHANGELOG, **raw.get("changelog", {})}
        self.sources = raw.get("source", [])
        global T
        T = STRINGS.get(self.repo["language"], STRINGS["en"])

    def repo_vars(self) -> dict[str, str]:
        url = self.repo["url"]
        v = dict(self.vars)
        v.update(
            repo_name=self.repo["name"],
            repo_url=url,
            repo_description=self.repo["description"],
            repo_icon_url=f"{url}/{self.repo['icon']}",
        )
        for key in self.vars:
            v[key] = subst(v[key], v, f"[vars].{key}")
        return v


VAR_RE = re.compile(r"\{\{\s*([\w.-]+)\s*\}\}")


def subst(text: str, variables: dict[str, str], where: str) -> str:
    def replace(m):
        if m.group(1) in variables:
            return variables[m.group(1)]
        warn(f"{where}: 不認得的變數 {m.group(0)}")
        return m.group(0)

    return VAR_RE.sub(replace, text)


def ios_range(depends: str) -> tuple[str | None, str | None, bool]:
    lo = hi = None
    exclusive = False
    for op, version in re.findall(r"firmware\s*\(\s*(>=|>>|<=|<<|=)\s*([\d.]+)\s*\)", depends):
        if op in (">=", ">>", "="):
            lo = version
        if op in ("<=", "<<", "="):
            hi, exclusive = version, op == "<<"
    return lo, hi, exclusive


def dependency_names(depends: str) -> list[str]:
    names = []
    for part in depends.split(","):
        name = part.split("|")[0].strip().split(" ")[0].split("(")[0].strip()
        if name and name != "firmware":
            names.append(name)
    return names


# ---------------------------------------------------------------- 來源與更新紀錄


class Changelogs:
    """用兩個版本之間的 commit 產生更新紀錄。"""

    def __init__(self, cfg: Config, gh: GitHub):
        self.gh = gh
        self.ignore = [re.compile(p) for p in cfg.changelog["ignore"]]
        self.ignore_paths = cfg.changelog["ignore_paths"]
        self.bodies = cfg.changelog["commit_bodies"]

    def _ignored(self, text: str) -> bool:
        return any(p.search(text) for p in self.ignore)

    def _only_ignored_paths(self, repo: str, sha: str) -> bool:
        detail = self.gh.get(f"repos/{repo}/commits/{sha}", immutable=True) or {}
        files = [f["filename"] for f in detail.get("files", [])]
        return bool(files) and all(any(fnmatch.fnmatch(f, p) for p in self.ignore_paths) for f in files)

    def from_commits(self, repo: str, base: str, head: str) -> str | None:
        quote = functools.partial(urllib.parse.quote, safe="")
        compare = self.gh.get(f"repos/{repo}/compare/{quote(base)}...{quote(head)}", immutable=True) or {}
        lines = []
        for commit in compare.get("commits", []):
            if len(commit.get("parents", [])) > 1:
                continue
            subject, _, body = commit["commit"]["message"].strip().partition("\n")
            subject = re.sub(r"^v?\d+(\.\d+)+\s*[:：]\s*", "", subject.strip())
            if not subject or self._ignored(subject) or self._only_ignored_paths(repo, commit["sha"]):
                continue
            item = [f"- {subject}"]
            if self.bodies:
                in_bullet = False
                for raw in body.splitlines():
                    line = raw.rstrip()
                    if re.match(r"^[\w-]+: .*<.*>$|^(Co-Authored-By|Signed-off-by|Change-Id):", line.strip(), re.I):
                        continue
                    bullet = re.match(r"^\s*[-*•]\s+(.*)", line)
                    if bullet:
                        in_bullet = not self._ignored(bullet.group(1))
                        if in_bullet:
                            item.append(f"  - {bullet.group(1)}")
                    elif in_bullet and line.startswith((" ", "\t")) and line.strip():
                        item[-1] += " " + line.strip()
                    else:
                        in_bullet = False
            lines.append("\n".join(item))
        return "\n".join(lines) or None


def load_source(conf: dict, gh: GitHub, readme: bool = True) -> Source:
    repo = conf["github"]
    source = Source(repo, conf, gh.get(f"repos/{repo}") or {})
    if not source.info:
        warn(f"{repo}: 找不到這個 repo（私有或打錯？）")
        return source
    branch = gh.get(f"repos/{repo}/branches/{urllib.parse.quote(source.branch, safe='')}") or {}
    source.head = (branch.get("commit") or {}).get("sha")
    if source.head:
        text = gh.file(repo, conf.get("control", "control"), source.head)
        source.control = parse_control(text) if text else None
    if not source.control:
        warn(f"{repo}: {source.branch} 上找不到 {conf.get('control', 'control')}")
    if readme and conf.get("readme", True):
        data = gh.get(f"repos/{repo}/readme")
        if data and data.get("content"):
            source.readme = base64.b64decode(data["content"]).decode("utf-8", "replace")
    return source


BUILDS = ROOT / "debs" / "builds.json"


def load_builds() -> dict[str, dict]:
    """debs/builds.json：每個 .deb 從哪個 repo 的哪個 commit 編出來、什麼時候上架。"""
    return json.loads(BUILDS.read_text()) if BUILDS.exists() else {}


def save_builds(builds: dict[str, dict]) -> None:
    BUILDS.write_text(json.dumps(dict(sorted(builds.items())), indent=2, ensure_ascii=False) + "\n")


def git_time(*args: str) -> dt.datetime | None:
    try:
        out = subprocess.run(["git", "-C", str(ROOT), "log", *args], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    lines = out.stdout.split()
    return parse_time(lines[-1]) if out.returncode == 0 and lines else None


def load_debs(sources: dict[str, Source], logs: Changelogs) -> list[Deb]:
    builds = load_builds()
    by_package = {s.control["Package"]: s for s in sources.values() if s.control and s.control.get("Package")}
    debs = []
    for path in sorted((ROOT / "debs").glob("*.deb")):
        control = read_control(path)
        record = builds.get(path.name, {})
        date = parse_time(record["date"]) if record.get("date") else (
            git_time("--follow", "--diff-filter=A", "--format=%cI", "--", str(path.relative_to(ROOT)))
            or dt.datetime.fromtimestamp(path.stat().st_mtime, UTC))
        source = sources.get(record.get("repo", "")) or by_package.get(control.get("Package", ""))
        debs.append(Deb(path, control, date, source, record.get("commit")))

    # 同一個來源的版本由舊到新，每版的更新紀錄 = 上一版的 commit 到這一版的 commit
    for source in sources.values():
        if source.conf.get("changelog", "auto") == "none":
            continue
        built = sorted((d for d in debs if d.source is source and d.commit), key=lambda d: version_key(d.version))
        for package in dict.fromkeys(d.package for d in built):
            previous = None
            for deb in (d for d in built if d.package == package):
                deb.notes = logs.from_commits(source.repo, previous.commit, deb.commit) if previous else INITIAL
                deb.auto_notes = True
                previous = deb
    info(f"debs/: {len(debs)} 個 .deb")
    return debs


def makefile_sdk(makefile: str | None) -> str | None:
    m = re.search(r"^\s*TARGET\s*[:?]?=\s*iphone:clang:([\d.]+)", makefile or "", re.M)
    return m.group(1) if m else None


def pending(cfg: Config, gh: GitHub, retry: bool) -> list[dict]:
    """預設分支的 control 版本還沒有 .deb 的來源，給 workflow 去編。"""
    have = {(c.get("Package"), c.get("Version")) for c in map(read_control, (ROOT / "debs").glob("*.deb"))}
    attempted_file = CACHE / "attempted.json"
    attempted = json.loads(attempted_file.read_text()) if attempted_file.exists() and not retry else []
    jobs = []
    for conf in cfg.sources:
        if conf.get("build", True) is False:
            continue
        source = load_source(conf, gh, readme=False)
        control = source.control
        if not control or (control.get("Package"), control.get("Version")) in have:
            continue
        key = f"{source.repo}@{source.head}"
        if key in attempted:
            info(f"{key} 編過但失敗了；push 新的 commit 或手動執行 workflow 會再試")
            continue
        sdk = str(conf.get("sdk") or makefile_sdk(gh.file(source.repo, "Makefile", source.head)) or "")
        if not sdk:
            warn(f"{source.repo}: Makefile 的 TARGET 沒寫 SDK 版本，請在 [[source]] 加 sdk = \"16.5\"")
            continue
        jobs.append({
            "repo": source.repo,
            "sha": source.head,
            "package": control["Package"],
            "version": control["Version"],
            "name": control.get("Name") or control["Package"],
            "sdk": sdk,
            "make": conf.get("make", "package FINALPACKAGE=1"),
        })
        attempted.append(key)
        info(f"{source.repo}: 要編 {control['Package']} {control['Version']}（{source.head[:7]}）")
    CACHE.mkdir(exist_ok=True)
    attempted_file.write_text(json.dumps(attempted[-200:]))
    return jobs


def record(folders: list[Path]) -> list[str]:
    """把 workflow 編好的 .deb 放進 debs/，並記到 builds.json。"""
    builds = load_builds()
    added = []
    for folder in folders:
        if not (folder / "build.json").is_file():
            continue
        job = json.loads((folder / "build.json").read_text())
        for path in sorted(folder.rglob("*.deb")):
            control = read_control(path)
            if (control.get("Package"), control.get("Version")) != (job["package"], job["version"]):
                continue
            shutil.copy2(path, ROOT / "debs" / path.name)
            builds[path.name] = {"repo": job["repo"], "commit": job["sha"],
                                 "date": dt.datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}
            added.append(f"{job['name']} {job['version']}")
    save_builds(builds)
    return list(dict.fromkeys(added))


def parse_changelog_md(path: Path) -> dict[str, tuple[dt.datetime | None, str]]:
    if not path.is_file():
        return {}
    entries: dict[str, list] = {}
    current = None
    for line in path.read_text().splitlines():
        m = re.match(r"^##\s+v?([^\s(]+)\s*(?:[-–(]\s*(\d{4}-\d{2}-\d{2})\s*\)?)?\s*$", line)
        if m:
            current = m.group(1)
            date = dt.datetime.fromisoformat(m.group(2)).replace(tzinfo=UTC) if m.group(2) else None
            entries[current] = [date, []]
        elif current:
            entries[current][1].append(line)
    return {v: (date, "\n".join(lines).strip()) for v, (date, lines) in entries.items()}


def readme_description(source: Source, tagline: str) -> tuple[str, list[str]]:
    """README 去掉標題、建置/安裝/授權等段落；圖片拿出來當截圖，相對連結改成絕對網址。"""
    blob = f"https://github.com/{source.repo}/blob/{source.branch}/README.md"
    raw = f"https://raw.githubusercontent.com/{source.repo}/{source.branch}/README.md"
    images: list[str] = []

    def take_image(src: str) -> str:
        url = urllib.parse.urljoin(raw, src.strip().split(" ")[0])
        if re.search(r"(?i)\.(png|jpe?g|webp|gif)(\?|$)", url) and not re.search(r"(?i)shields\.io|badge", url):
            images.append(url)
        return ""

    text = source.readme or ""
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"!\[[^\]]*\]\(([^)]+)\)", lambda m: take_image(m.group(1)), text)
    text = re.sub(r"<img\b[^>]*?\bsrc=[\"']([^\"']+)[\"'][^>]*>", lambda m: take_image(m.group(1)), text)
    text = re.sub(r"</?(p|div|br|center|picture|source|a)\b[^>]*>", "", text)
    text = re.sub(r"(?<!!)\[([^\]]+)\]\(([^)\s]+)\)",
                  lambda m: m.group(0) if re.match(r"^([a-z][\w+.-]*:|#)", m.group(2))
                  else f"[{m.group(1)}]({urllib.parse.urljoin(blob, m.group(2))})", text)

    kept: list[str] = []
    dropping_level = None
    seen_title = in_fence = False
    for line in text.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
        heading = None if in_fence else re.match(r"^(#{1,6})\s+(.*?)\s*#*\s*$", line)
        if heading:
            level, title = len(heading.group(1)), heading.group(2)
            if dropping_level is not None and level > dropping_level:
                continue
            dropping_level = None
            if level == 1 and not seen_title:
                seen_title = True
                continue
            if README_DROP.search(title):
                dropping_level = level
                continue
        elif dropping_level is not None:
            continue
        kept.append(line)

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", "\n".join(kept)) if p.strip()]
    plain = lambda s: re.sub(r"[\W_]+", "", s).lower()  # noqa: E731
    if paragraphs and tagline and plain(paragraphs[0]) == plain(tagline):
        paragraphs.pop(0)
    return "\n\n".join(paragraphs), images


# ---------------------------------------------------------------- 套件


class Package:
    def __init__(self, cfg: Config, gh: GitHub, pid: str, debs: list[Deb], meta: dict):
        self.cfg, self.gh, self.id, self.debs, self.meta = cfg, gh, pid, debs, meta
        self.dir = ROOT / "meta" / pid
        self.latest = debs[0]
        self.source = next((d.source for d in debs if d.source), None)
        self.src_info = self.source.info if self.source else {}
        control = self.latest.control
        repo_url = cfg.repo["url"]

        self.name = meta.get("name") or control.get("Name") or pid
        tagline = meta.get("tagline") or control.get("Description", "").split("\n")[0].strip()
        if not meta.get("tagline") and (not tagline or PLACEHOLDER_DESCRIPTION.match(tagline)):
            tagline = self.src_info.get("description") or tagline
        self.tagline = tagline
        self.author = meta.get("author") or re.sub(r"\s*<[^>]*>", "", control.get("Author") or control.get("Maintainer") or "")
        self.section = meta.get("section") or control.get("Section") or "Tweaks"
        self.tint = meta.get("tint") or cfg.repo["tint"]
        lo, hi, exclusive = ios_range(control.get("Depends", "") + "," + control.get("Pre-Depends", ""))
        self.min_ios = str(meta.get("min_ios") or lo or "") or None
        if "max_ios" in meta:
            self.max_ios, self.max_exclusive = str(meta["max_ios"]), False
        else:
            self.max_ios, self.max_exclusive = hi, exclusive
        self.homepage = meta.get("homepage") or (self.source.url if self.source else None)
        self.requires = dependency_names(control.get("Depends", ""))
        self.depiction_url = f"{repo_url}/depictions/{pid}/"
        self.sileo_depiction_url = f"{repo_url}/depictions/{pid}/depiction.json"

        self.icon = self._image("icon", ICON_PATTERNS)
        self.header = self._image("header", HEADER_PATTERNS)
        self.banner = self._image("banner", None) or self.header

        description, readme_images = "", []
        if "description" in meta:
            description = meta["description"]
        elif (self.dir / "description.md").is_file():
            description = (self.dir / "description.md").read_text()
        elif self.source and self.source.readme:
            description, readme_images = readme_description(self.source, self.tagline)
        elif "\n" in control.get("Description", ""):
            description = "\n".join(l.strip() if l.strip() != "." else "" for l in control["Description"].split("\n")[1:])
        self.screenshots = self._screenshots(readme_images)

        self.vars = self._variables()
        where = f"meta/{pid}"
        self.tagline = subst(self.tagline, self.vars, where)
        self.vars["tagline"] = self.tagline
        self.description = subst(description.strip(), self.vars, where)
        self.changelog = self._changelog()
        self.links = self._links()
        self.info_rows = self._info_rows()

    # 圖片 -------------------------------------------------------------

    def _image(self, kind: str, deb_patterns: list[str] | None) -> Image | None:
        spec = self.meta.get(kind)
        if spec and re.match(r"^https?://", spec):
            return Image("", url=spec)
        candidates = [self.dir / spec] if spec else [self.dir / f"{kind}.{ext}" for ext in ("png", "jpg", "jpeg")]
        for path in candidates:
            if path.is_file():
                data = path.read_bytes()
                return Image(f"{kind}{path.suffix.lower()}", data, size=image_size(data))
        if spec:
            warn(f"meta/{self.id}: 找不到 {kind} 圖片 {spec}")
        if deb_patterns:
            data = find_in_deb(self.latest.path, deb_patterns)
            if data:
                return Image(f"{kind}.png", data, size=image_size(data))
        return None

    def _screenshots(self, readme_images: list[str]) -> list[Image]:
        specs = self.meta.get("screenshots")
        if specs is None:
            folder = self.dir / "screenshots"
            specs = [str(p.relative_to(self.dir)) for p in sorted(folder.iterdir()) if p.suffix.lower()
                     in (".png", ".jpg", ".jpeg")] if folder.is_dir() else []
        shots = []

        def add(data: bytes, suffix: str) -> None:
            data, suffix = compact_screenshot(data, suffix)
            shots.append(Image(f"screenshots/{len(shots) + 1}{suffix}", data, size=image_size(data)))

        for spec in specs:
            if re.match(r"^https?://", spec):
                shots.append(Image("", url=spec))
            elif (self.dir / spec).is_file():
                add((self.dir / spec).read_bytes(), Path(spec).suffix.lower())
            else:
                warn(f"meta/{self.id}: 找不到截圖 {spec}")
        if not specs:
            for url in readme_images:
                cached = CACHE / "images" / hashlib.sha256(url.encode()).hexdigest()[:16]
                try:
                    data = self.gh.download(url, cached).read_bytes()
                except (OSError, RuntimeError, urllib.error.URLError) as e:
                    warn(f"{self.id}: README 圖片抓不到 {url}（{e}）")
                    continue
                add(data, Path(urllib.parse.urlparse(url).path).suffix.lower() or ".png")
        return shots

    def image_url(self, image: Image | None, root: str | None = None) -> str:
        """root 是網站根目錄的網址或相對路徑；預設絕對網址（Packages、Sileo 用）。"""
        if not image:
            return ""
        root = f"{self.cfg.repo['url']}/" if root is None else root
        return image.url or f"{root}depictions/{self.id}/{image.name}"

    def images(self) -> list[Image]:
        return [i for i in (self.icon, self.header, self.banner, *self.screenshots) if i and i.data]

    # 文字 -------------------------------------------------------------

    @property
    def versions(self) -> list[str]:
        return list(dict.fromkeys(d.version for d in self.debs))

    def jailbreaks(self) -> str:
        archs = dict.fromkeys(d.arch for d in self.debs if d.version == self.latest.version)
        return ", ".join(JAILBREAKS.get(a, a) for a in archs)

    def compatibility(self) -> str:
        top = f"<{self.max_ios}" if self.max_exclusive else self.max_ios
        if self.min_ios and self.max_ios:
            return T["ios_range"].format(min=self.min_ios, max=top)
        if self.min_ios:
            return T["ios_min"].format(min=self.min_ios)
        if self.max_ios:
            return T["ios_max"].format(max=top)
        return ""

    def _variables(self) -> dict[str, str]:
        latest, control = self.latest, self.latest.control
        source = self.source
        parent = self.src_info.get("parent") or {}
        v = self.cfg.repo_vars()
        v.update(
            package=self.id,
            name=self.name,
            tagline=self.tagline,
            author=self.author,
            maintainer=control.get("Maintainer", ""),
            section=self.section,
            version=latest.version,
            architecture=latest.arch,
            jailbreak=self.jailbreaks(),
            min_ios=self.min_ios or "",
            max_ios=self.max_ios or "",
            compatibility=self.compatibility(),
            depends=", ".join(self.requires),
            size=human_size(latest.path.stat().st_size),
            installed_size=human_size(int(control.get("Installed-Size", "0") or 0) * 1024),
            updated=day(latest.date),
            released=day(min(d.date for d in self.debs)),
            version_count=str(len(self.versions)),
            depiction_url=self.depiction_url,
            sileo_depiction_url=self.sileo_depiction_url,
            icon_url=self.image_url(self.icon),
            header_url=self.image_url(self.header),
            download_url=f"{self.cfg.repo['url']}/debs/{latest.path.name}",
            sileo_url=f"sileo://package/{self.id}",
            zebra_url=f"zbra://packages/{self.id}",
            homepage=self.homepage or "",
            source_repo=source.repo if source else "",
            source_url=source.url if source else "",
            issues_url=f"{source.url}/issues" if source else "",
            commit=latest.commit or "",
            commit_url=f"{source.url}/commit/{latest.commit}" if source and latest.commit else "",
            upstream_repo=parent.get("full_name", ""),
            upstream_url=parent.get("html_url", ""),
        )
        custom = {k: str(x) for k, x in self.meta.get("vars", {}).items()}
        v.update(custom)
        for key in custom:
            v[key] = subst(v[key], v, f"meta/{self.id} [vars].{key}")
        return v

    def _changelog(self) -> list[Entry]:
        manual = parse_changelog_md(self.dir / "changelog.md")
        versions = self.versions
        entries = []
        for version in versions:
            debs = [d for d in self.debs if d.version == version]
            date = min(d.date for d in debs)
            deb = next((d for d in debs if d.notes), None)
            notes, auto = (deb.notes, deb.auto_notes) if deb else (None, False)
            if notes == INITIAL:
                notes, auto = (T["initial_release"] if version == versions[-1] else None), False
            if version in manual:
                date = manual[version][0] or date
                if manual[version][1]:
                    notes, auto = manual[version][1], False
            entries.append(Entry(version, date, notes, auto))
        for version, (date, notes) in manual.items():
            if version not in versions:
                entries.append(Entry(version, date, notes))
        entries.sort(key=lambda e: version_key(e.version))

        # tag 不在同一條線上時，同一個 commit 會出現在兩個版本；自動產生的只留最早那次
        def items(notes: str) -> list[str]:
            blocks: list[list[str]] = []
            for line in notes.splitlines():
                if line.startswith("- ") or not blocks:
                    blocks.append([])
                blocks[-1].append(line)
            return ["\n".join(b) for b in blocks]

        def key(item: str) -> str:
            return re.sub(r"[\W_]+", "", item.split("\n")[0]).lower()

        seen: set[str] = set()
        for e in entries:
            if e.notes and e.auto:
                e.notes = "\n".join(i for i in items(e.notes) if key(i) not in seen) or None
            seen.update(key(i) for i in items(e.notes or ""))
            e.notes = subst(e.notes or T["no_notes"], self.vars, f"meta/{self.id}/changelog.md")
        return entries[::-1]

    def _links(self) -> list[tuple[str, str]]:
        links = []
        if self.source:
            links.append((T["source_code"], self.source.url))
            if self.src_info.get("has_issues"):
                links.append((T["report_issue"], f"{self.source.url}/issues"))
            parent = self.src_info.get("parent")
            if parent:
                links.append((T["based_on"].format(repo=parent["full_name"]), parent["html_url"]))
        if self.homepage and self.homepage != (self.source.url if self.source else None):
            links.append((T["homepage"], self.homepage))
        for link in self.meta.get("link", []):
            where = f"meta/{self.id} [[link]]"
            links.append((subst(link["name"], self.vars, where), subst(link["url"], self.vars, where)))
        return links

    def _info_rows(self) -> list[tuple[str, str]]:
        rows = [
            (T["version"], self.latest.version),
            (T["updated"], day(self.latest.date)),
            (T["size"], self.vars["size"]),
            (T["compatibility"], self.compatibility()),
            (T["jailbreak"], self.jailbreaks()),
            (T["author"], self.author),
            (T["section"], self.section),
            (T["requires"], ", ".join(self.requires)),
        ]
        for row in self.meta.get("info", []):
            where = f"meta/{self.id} [[info]]"
            rows.append((subst(row["title"], self.vars, where), subst(row["text"], self.vars, where)))
        return [(k, v) for k, v in rows if v]

    # Packages ---------------------------------------------------------

    def stanza(self, deb: Deb) -> str:
        fields = {"Package": deb.package}
        fields.update((k, v) for k, v in deb.control.items() if k != "Package")
        fields["Name"] = self.name
        if self.tagline:
            fields["Description"] = self.tagline
        if self.meta.get("author"):
            fields["Author"] = self.meta["author"]
        if self.meta.get("maintainer"):
            fields["Maintainer"] = self.meta["maintainer"]
        fields["Section"] = self.section
        fields["Depiction"] = self.depiction_url
        fields["SileoDepiction"] = self.sileo_depiction_url
        if self.icon:
            fields["Icon"] = self.image_url(self.icon)
        if self.header:
            fields["Header"] = self.image_url(self.header)
        if self.homepage:
            fields["Homepage"] = self.homepage
        tags = [t.strip() for t in fields.get("Tag", "").split(",") if t.strip()]
        tags += [t for t in self.meta.get("tags", []) if t not in tags]
        if tags:
            fields["Tag"] = ", ".join(tags)
        for key, value in self.meta.get("control", {}).items():
            value = subst(str(value), self.vars, f"meta/{self.id} [control].{key}")
            if value:
                fields[key] = value
            else:
                fields.pop(key, None)
        data = deb.path.read_bytes()
        fields["Filename"] = f"debs/{deb.path.name}"
        fields["Size"] = str(len(data))
        fields["MD5sum"] = hashlib.md5(data).hexdigest()
        fields["SHA1"] = hashlib.sha1(data).hexdigest()
        fields["SHA256"] = hashlib.sha256(data).hexdigest()
        fields["SHA512"] = hashlib.sha512(data).hexdigest()
        return "\n".join(f"{k}: {v}" for k, v in fields.items())


def assemble(cfg: Config, gh: GitHub, debs: list[Deb]) -> list[Package]:
    seen: dict[tuple, Deb] = {}
    by_id: dict[str, list[Deb]] = {}
    for deb in debs:
        key = (deb.package, deb.version, deb.arch)
        if key in seen:
            warn(f"{deb.package} {deb.version} ({deb.arch}) 重複：保留 {seen[key].path.name}，略過 {deb.path}")
            continue
        seen[key] = deb
        by_id.setdefault(deb.package, []).append(deb)

    packages = []
    for pid, items in sorted(by_id.items()):
        meta_file = ROOT / "meta" / pid / "package.toml"
        meta = load_toml(meta_file) if meta_file.is_file() else {}
        if meta.get("hidden"):
            info(f"{pid}: hidden，不上架")
            continue
        hide = {str(v) for v in meta.get("hide_versions", [])}
        items = [d for d in items if d.version not in hide]
        items.sort(key=lambda d: (version_key(d.version), d.date), reverse=True)
        if meta.get("keep_versions"):
            keep = list(dict.fromkeys(d.version for d in items))[:int(meta["keep_versions"])]
            items = [d for d in items if d.version in keep]
        if items:
            packages.append(Package(cfg, gh, pid, items, meta))
    for meta_dir in sorted((ROOT / "meta").glob("*/")):
        if meta_dir.name not in by_id:
            warn(f"meta/{meta_dir.name}: 沒有對應的 .deb")
    return packages


# ---------------------------------------------------------------- Markdown → HTML


def md_inline(text: str) -> str:
    text = html.escape(text, quote=False)
    codes: list[str] = []

    def keep_code(m):
        codes.append(f"<code>{m.group(1)}</code>")
        return f"\x00{len(codes) - 1}\x00"

    def href(url: str) -> str:
        return url.replace('"', "%22")

    text = re.sub(r"`([^`]+)`", keep_code, text)
    text = re.sub(r"!\[([^\]]*)\]\(([^)\s]+)\)", lambda m: f'<img src="{href(m.group(2))}" alt="{m.group(1)}">', text)
    text = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", lambda m: f'<a href="{href(m.group(2))}">{m.group(1)}</a>', text)
    text = re.sub(r"&lt;(https?://[^\s&]+)&gt;", r'<a href="\1">\1</a>', text)
    text = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: f"<strong>{m.group(1) or m.group(2)}</strong>", text)
    text = re.sub(r"(?<![\w*])\*(?![\s*])(.+?)(?<![\s*])\*(?![\w*])|(?<!\w)_(?!\s)(.+?)(?<!\s)_(?!\w)",
                  lambda m: f"<em>{m.group(1) or m.group(2)}</em>", text)
    text = re.sub(r"~~(.+?)~~", r"<del>\1</del>", text)
    return re.sub(r"\x00(\d+)\x00", lambda m: codes[int(m.group(1))], text)


def md_to_html(md: str) -> str:
    out: list[str] = []
    para: list[str] = []
    lists: list[list] = []   # [縮排, 標籤]
    lines = md.replace("\r\n", "\n").split("\n")

    def flush_para():
        if para:
            out.append(f"<p>{md_inline(' '.join(para))}</p>")
            para.clear()

    def close_lists(indent: int = -1):
        while lists and lists[-1][0] > indent:
            out.append(f"</li></{lists.pop()[1]}>")

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        item = re.match(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$", line)
        if stripped.startswith(("```", "~~~")):
            flush_para()
            close_lists()
            fence, code = stripped[:3], []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith(fence):
                code.append(lines[i])
                i += 1
            out.append(f"<pre><code>{html.escape(chr(10).join(code))}</code></pre>")
        elif not stripped:
            flush_para()
            following = next((l for l in lines[i + 1:] if l.strip()), "")
            if lists and not re.match(r"^\s+\S|^\s*([-*+]|\d+[.)])\s", following):
                close_lists()
        elif re.match(r"^(\*\s*){3,}$|^(-\s*){3,}$|^(_\s*){3,}$", stripped) and not lists:
            flush_para()
            out.append("<hr>")
        elif m := re.match(r"^(#{1,6})\s+(.*?)\s*#*$", stripped):
            flush_para()
            close_lists()
            level = min(len(m.group(1)) + 1, 6)
            out.append(f"<h{level}>{md_inline(m.group(2))}</h{level}>")
        elif item:
            flush_para()
            indent = len(item.group(1).replace("\t", "    "))
            tag = "ol" if item.group(2)[0].isdigit() else "ul"
            close_lists(indent)
            if lists and lists[-1][0] == indent and lists[-1][1] == tag:
                out.append("</li>")
            else:
                if lists and lists[-1][0] == indent:
                    out.append(f"</li></{lists.pop()[1]}>")
                out.append(f"<{tag}>")
                lists.append([indent, tag])
            out.append(f"<li>{md_inline(item.group(3))}")
        elif lists and line[:1] in (" ", "\t"):
            out[-1] += " " + md_inline(stripped)
        elif stripped.startswith(">"):
            flush_para()
            close_lists()
            quote = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quote.append(re.sub(r"^\s*>\s?", "", lines[i]))
                i += 1
            out.append(f"<blockquote>{md_to_html(chr(10).join(quote))}</blockquote>")
            continue
        elif stripped.startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|?\s*:?-{2,}", lines[i + 1]):
            flush_para()
            close_lists()
            cells = lambda row: [c.strip() for c in row.strip().strip("|").split("|")]  # noqa: E731
            table = ["<table><thead><tr>"] + [f"<th>{md_inline(c)}</th>" for c in cells(line)] + ["</tr></thead><tbody>"]
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                table += ["<tr>"] + [f"<td>{md_inline(c)}</td>" for c in cells(lines[i])] + ["</tr>"]
                i += 1
            out.append("".join(table) + "</tbody></table>")
            continue
        else:
            if lists:
                close_lists()
            para.append(stripped + ("<br>" if line.endswith("  ") else ""))
        i += 1
    flush_para()
    close_lists()
    return "\n".join(out).replace(html.escape("<br>"), "<br>")


# ---------------------------------------------------------------- 輸出


def native_depiction(p: Package) -> dict:
    def markdown(text: str) -> dict:
        return {"class": "DepictionMarkdownView", "markdown": text, "useSpacing": True}

    details: list[dict] = []
    if p.description:
        details.append(markdown(p.description))
    if p.screenshots:
        w, h = next((s.size for s in p.screenshots if s.size), (9, 19.5))
        height = 320
        details.append({
            "class": "DepictionScreenshotsView",
            "itemCornerRadius": 10,
            "itemSize": f"{{{round(height * w / h)}, {height}}}",
            "screenshots": [{"url": p.image_url(s), "accessibilityText": T["screenshot"].format(n=i)}
                            for i, s in enumerate(p.screenshots, 1)],
        })
    details.append({"class": "DepictionHeaderView", "title": T["information"]})
    details += [{"class": "DepictionTableTextView", "title": k, "text": v} for k, v in p.info_rows]
    if p.links:
        details.append({"class": "DepictionHeaderView", "title": T["links"]})
        details += [{"class": "DepictionTableButtonView", "title": name, "action": url, "openExternal": True}
                    for name, url in p.links]
    details.append({"class": "DepictionSpacerView", "spacing": 12})
    details.append({"class": "DepictionLabelView", "text": p.cfg.repo["name"], "alignment": 1,
                    "fontSize": 12, "textColor": "#8E8E93"})

    changelog: list[dict] = []
    for e in p.changelog:
        changelog.append({"class": "DepictionHeaderView", "title": e.version, "useBottomMargin": False})
        if e.date:
            changelog.append({"class": "DepictionSubheaderView", "title": day(e.date), "useBottomMargin": False})
        changelog.append(markdown(e.notes))
        changelog.append({"class": "DepictionSeparatorView"})
    if changelog:
        changelog.pop()

    depiction = {
        "minVersion": "0.1",
        "class": "DepictionTabView",
        "tintColor": p.tint,
        "tabs": [
            {"tabname": T["details"], "class": "DepictionStackView", "tintColor": p.tint, "views": details},
            {"tabname": T["changelog"], "class": "DepictionStackView", "tintColor": p.tint, "views": changelog},
        ],
    }
    if p.header:
        depiction["headerImage"] = p.image_url(p.header)
    return depiction


def page(title: str, body: str, *, root: str, tint: str, icon: str, lang: str, description: str = "") -> str:
    return f"""<!doctype html>
<html lang="{esc(lang)}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="color-scheme" content="light dark">
<title>{esc(title)}</title>
<meta name="description" content="{esc(description)}">
<link rel="icon" href="{esc(icon)}">
<link rel="apple-touch-icon" href="{esc(icon)}">
<link rel="stylesheet" href="{root}assets/style.css">
<style>:root {{ --tint: {esc(tint)}; }}</style>
</head>
<body>
{body}
<script src="{root}assets/site.js"></script>
</body>
</html>
"""


def footer(cfg: Config) -> str:
    variables = cfg.repo_vars()
    links = "".join(
        f'<a href="{esc(subst(l["url"], variables, "[[link]]"))}">{esc(subst(l["name"], variables, "[[link]]"))}</a>'
        for l in cfg.links)
    return f'<footer class="foot"><a href="{esc(cfg.repo["url"])}/">{esc(cfg.repo["name"])}</a>{links}</footer>'


def web_depiction(p: Package) -> str:
    cfg = p.cfg
    icon = p.image_url(p.icon, "../../") or f"../../{cfg.repo['icon']}"
    parts = []
    if p.header:
        parts.append(f'<img class="banner" src="{esc(p.image_url(p.header, "../../"))}" alt="">')
    parts.append('<main class="wrap">')
    parts.append(f"""<header class="pkg-head">
  <img class="icon" src="{esc(icon)}" alt="" width="72" height="72">
  <div>
    <h1>{esc(p.name)}</h1>
    <p class="muted">{esc(p.author)}</p>
  </div>
</header>""")
    if p.tagline:
        parts.append(f'<p class="tagline">{esc(p.tagline)}</p>')
    parts.append(f"""<div class="actions">
  <a class="button" href="sileo://package/{esc(p.id)}">{esc(T["open_in_sileo"])}</a>
  <a class="button secondary" href="../../debs/{esc(p.latest.path.name)}">{esc(T["download_deb"])}</a>
</div>""")
    if p.min_ios or p.max_ios:
        top = p.max_ios or ""
        parts.append(
            f'<p id="compat" class="compat" hidden data-min="{esc(p.min_ios or "")}" data-max="{esc(top)}"'
            f' data-max-exclusive="{int(p.max_exclusive)}"'
            f' data-ok="{esc(T["ok"])}"'
            f' data-too-old="{esc(T["too_old"].replace("{min}", p.min_ios or ""))}"'
            f' data-too-new="{esc(T["too_new"].replace("{max}", ("<" if p.max_exclusive else "") + top))}"></p>')
    parts.append(f"""<nav class="tabs" role="tablist">
  <a href="#details" data-tab="details" role="tab">{esc(T["details"])}</a>
  <a href="#changelog" data-tab="changelog" role="tab">{esc(T["changelog"])}</a>
</nav>""")

    details = []
    if p.description:
        details.append(f'<div class="card prose">{md_to_html(p.description)}</div>')
    if p.screenshots:
        shots = "".join(
            f'<img src="{esc(p.image_url(s, "../../"))}" alt="{esc(T["screenshot"].format(n=i))}" loading="lazy"'
            + (f' width="{s.size[0]}" height="{s.size[1]}"' if s.size else "") + ">"
            for i, s in enumerate(p.screenshots, 1))
        details.append(f'<div class="shots">{shots}</div>')
    rows = "".join(f"<div><dt>{esc(k)}</dt><dd>{esc(v)}</dd></div>" for k, v in p.info_rows)
    details.append(f'<h2>{esc(T["information"])}</h2><dl class="card rows">{rows}</dl>')
    if p.links:
        links = "".join(f'<a href="{esc(url)}">{esc(name)}</a>' for name, url in p.links)
        details.append(f'<h2>{esc(T["links"])}</h2><nav class="card rows links">{links}</nav>')
    parts.append(f'<section id="details" role="tabpanel">{"".join(details)}</section>')

    entries = "".join(
        f"""<article class="card entry">
  <header><h3>{esc(e.version)}</h3><time>{esc(day(e.date))}</time></header>
  <div class="prose">{md_to_html(e.notes)}</div>
</article>""" for e in p.changelog)
    parts.append(f'<section id="changelog" role="tabpanel"><h2 class="tab-title">{esc(T["changelog"])}</h2>{entries}</section>')
    parts.append(footer(cfg))
    parts.append("</main>")
    return page(p.name, "\n".join(parts), root="../../", tint=p.tint, icon=icon,
                lang=cfg.repo["language"], description=p.tagline)


def index_page(cfg: Config, packages: list[Package]) -> str:
    R = cfg.repo
    url = R["url"]
    variables = cfg.repo_vars()
    description = subst(R["description"], variables, "[repo].description")
    buttons = "".join(f'<a class="button" href="{esc(link.format(url=url))}">{esc(T["add_to"].format(manager=name))}</a>'
                      for name, link in ADD_LINKS)

    def icon_of(p: Package) -> str:
        return p.image_url(p.icon, "") or R["icon"]

    rows = "".join(f"""<a class="pkg" href="depictions/{esc(p.id)}/">
  <img class="icon" src="{esc(icon_of(p))}" alt="" width="48" height="48" loading="lazy">
  <span class="pkg-text"><strong>{esc(p.name)}</strong><span class="muted">{esc(p.tagline)}</span></span>
  <span class="pkg-meta"><span class="badge">{esc(p.latest.version)}</span><time>{esc(day(p.latest.date))}</time></span>
</a>""" for p in sorted(packages, key=lambda p: p.name.lower()))

    recent = sorted(((e, p) for p in packages for e in p.changelog if e.date), key=lambda x: x[0].date, reverse=True)[:8]
    updates = "".join(f"""<article class="card entry">
  <header><a href="depictions/{esc(p.id)}/#changelog"><img class="icon" src="{esc(icon_of(p))}" alt="" width="28" height="28" loading="lazy"><h3>{esc(p.name)} {esc(e.version)}</h3></a><time>{esc(day(e.date))}</time></header>
  <div class="prose">{md_to_html(e.notes)}</div>
</article>""" for e, p in recent)

    body = f"""<main class="wrap">
<header class="repo-head">
  <img class="icon" src="{esc(R["icon"])}" alt="" width="64" height="64">
  <div>
    <h1>{esc(R["name"])}</h1>
    <p class="muted">{esc(description)}</p>
  </div>
</header>
<div class="add">{buttons}</div>
<div class="repo-url card"><code>{esc(url)}/</code><button type="button" data-copy="{esc(url)}/" data-copied="{esc(T["copied"])}">{esc(T["copy_url"])}</button></div>
<h2>{esc(T["packages"])}</h2>
<nav class="card list">{rows or f'<p class="muted">{esc(T["no_packages"])}</p>'}</nav>
{f'<h2>{esc(T["recent_updates"])}</h2>{updates}' if updates else ""}
{footer(cfg)}
</main>"""
    return page(R["name"], body, root="", tint=R["tint"], icon=R["icon"], lang=R["language"], description=description)


def featured(packages: list[Package]) -> dict:
    banners = [{"url": p.image_url(p.banner), "title": p.name, "package": p.id, "hideShadow": False}
               for p in sorted(packages, key=lambda p: p.latest.date, reverse=True)
               if p.banner and p.meta.get("featured", True)]
    return {"class": "FeaturedBannersView", "itemSize": "{263, 148}", "itemCornerRadius": 10, "banners": banners}


def _zstd(data: bytes) -> bytes | None:
    try:
        from compression import zstd  # Python 3.14+
        return zstd.compress(data, level=19)
    except ImportError:
        pass
    try:
        return subprocess.run(["zstd", "-19", "-q", "-c"], input=data, capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        warn("沒有 zstd，略過 Packages.zst")
        return None


def write_indexes(cfg: Config, out: Path, stanzas: list[str], date: dt.datetime, archs: list[str]) -> None:
    packages = ("\n\n".join(stanzas) + "\n").encode() if stanzas else b""
    files = {
        "Packages": packages,
        "Packages.gz": gzip.compress(packages, compresslevel=9, mtime=0),
        "Packages.bz2": bz2.compress(packages, 9),
        "Packages.xz": lzma.compress(packages, preset=9),
    }
    if (zst := _zstd(packages)) is not None:
        files["Packages.zst"] = zst
    for name, data in files.items():
        (out / name).write_bytes(data)

    R = cfg.repo
    variables = cfg.repo_vars()
    lines = [
        f"Origin: {R['origin']}",
        f"Label: {R['label']}",
        f"Suite: {R['suite']}",
        f"Version: {R['version']}",
        f"Codename: {R['codename']}",
        f"Date: {email.utils.format_datetime(date, usegmt=True)}",
        f"Architectures: {' '.join(archs)}",
        "Components: main",
        f"Description: {subst(R['description'], variables, '[repo].description')}",
    ]
    for algo, title in (("md5", "MD5Sum"), ("sha1", "SHA1"), ("sha256", "SHA256"), ("sha512", "SHA512")):
        lines.append(f"{title}:")
        lines += [f" {hashlib.new(algo, data).hexdigest()} {len(data):>8} {name}" for name, data in files.items()]
    (out / "Release").write_text("\n".join(lines) + "\n")


def collect(cfg: Config, gh: GitHub) -> list[Package]:
    sources = {conf["github"]: load_source(conf, gh) for conf in cfg.sources}
    return assemble(cfg, gh, load_debs(sources, Changelogs(cfg, gh)))


def build(cfg: Config, out: Path, offline: bool) -> list[Package]:
    gh = GitHub(offline)
    packages = collect(cfg, gh)

    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(ROOT / "site", out)
    (out / "debs").mkdir()
    stanzas = []
    for p in packages:
        folder = out / "depictions" / p.id
        for image in p.images():
            (folder / image.name).parent.mkdir(parents=True, exist_ok=True)
            (folder / image.name).write_bytes(image.data)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "depiction.json").write_text(json.dumps(native_depiction(p), ensure_ascii=False, indent=2) + "\n")
        (folder / "index.html").write_text(web_depiction(p))
        for deb in p.debs:
            shutil.copy2(deb.path, out / "debs" / deb.path.name)
            stanzas.append(p.stanza(deb))

    archs = list(dict.fromkeys(cfg.repo["architectures"] + [d.arch for p in packages for d in p.debs]))
    dates = [d.date for p in packages for d in p.debs]
    head = git_time("-1", "--format=%cI")
    release_date = max(dates + ([head] if head else [])) if dates or head else dt.datetime.now(UTC)
    write_indexes(cfg, out, stanzas, release_date, archs)
    (out / "sileo-featured.json").write_text(json.dumps(featured(packages), ensure_ascii=False, indent=2) + "\n")
    (out / "index.html").write_text(index_page(cfg, packages))

    summary = [f"| {p.name} | `{p.id}` | {', '.join(p.versions)} | {day(p.latest.date)} |" for p in packages]
    info(f"完成：{len(packages)} 個套件、{len(stanzas)} 個 .deb → {out}")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write("| 套件 | ID | 版本 | 更新 |\n| --- | --- | --- | --- |\n" + "\n".join(summary) + "\n")
    return packages


# ---------------------------------------------------------------- CLI

PACKAGE_TEMPLATE = """\
# meta/{id}/package.toml：{id} 的額外設定。
# 全部都可以省略；沒寫的會自動從 .deb、GitHub repo 和 README 拿。
# 文字欄位都能用 {{{{變數}}}}，`python3 tools/repo.py vars {id}` 會列出全部和目前的值。

# name = "顯示名稱"
# tagline = "一句話介紹（Sileo 列表裡名稱下面那行）"
# description = \"\"\"
# 長介紹，Markdown。也可以寫在同資料夾的 description.md。
# \"\"\"
# author = "作者"
# maintainer = "維護者 <email>"
# section = "Tweaks"
# tint = "#FF9500"
# min_ios = "15.0"                 # 預設讀 Depends 裡的 firmware (>= x)
# max_ios = "17.0"
# homepage = "https://…"
# tags = ["compatible_rootless"]

# 圖片：路徑（相對這個資料夾）或網址。沒寫就找這裡的 icon.png、header.png、
# banner.png、screenshots/，再沒有就從 .deb 的設定 bundle 拿 icon 和 header。
# icon = "icon.png"
# header = "header.png"
# banner = "banner.png"            # Sileo 精選橫幅，沒有就用 header
# screenshots = ["screenshots/1.png", "screenshots/2.png"]

# featured = true                  # 放進 Sileo 精選
# hidden = false                   # 整個套件不上架
# hide_versions = ["1.0"]          # 這些版本不上架
# keep_versions = 5                # 只留最新幾個版本

# [vars]                           # 自訂變數，只在這個套件有效
# discord = "https://discord.gg/…"

# [control]                        # 直接寫進 Packages 的欄位（蓋掉 .deb 的值；寫 "" 就是刪掉）
# Conflicts = "com.example.other"

# [[info]]                         # 資訊表多一列
# title = "Settings"
# text = "Settings › {{{{name}}}}"

# [[link]]                         # 連結區多一個按鈕
# name = "Discord"
# url = "{{{{discord}}}}"

# 更新紀錄寫在同資料夾的 changelog.md，會蓋過自動產生的：
#   ## 1.2.0 (2026-10-01)
#   - 新功能…
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="repo.y00e.org 產生器")
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build", help="產生整個網站")
    b.add_argument("--out", default="_site", help="輸出資料夾（預設 _site）")
    b.add_argument("--offline", action="store_true", help="不連網，只用 .cache/")
    v = sub.add_parser("vars", help="列出可用的 {{變數}}")
    v.add_argument("package", nargs="?")
    v.add_argument("--offline", action="store_true")
    n = sub.add_parser("new", help="建立 meta/<套件>/package.toml 範本")
    n.add_argument("package")
    p = sub.add_parser("pending", help="（workflow 用）列出要編的新版本，JSON")
    p.add_argument("--retry", action="store_true", help="上次編失敗的也再試")
    r = sub.add_parser("record", help="（workflow 用）把編好的 .deb 加進 debs/")
    r.add_argument("folders", nargs="*", type=Path)
    args = parser.parse_args()

    cfg = Config(ROOT / "repo.toml")
    if args.command == "build":
        build(cfg, ROOT / args.out, args.offline)
    elif args.command == "pending":
        print(json.dumps(pending(cfg, GitHub(False), args.retry)))
    elif args.command == "record":
        print(", ".join(record(args.folders)))
    elif args.command == "new":
        path = ROOT / "meta" / args.package / "package.toml"
        if path.exists():
            sys.exit(f"{path.relative_to(ROOT)} 已經存在")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(PACKAGE_TEMPLATE.format(id=args.package))
        print(path.relative_to(ROOT))
    else:
        packages = collect(cfg, GitHub(args.offline))
        chosen = next((p for p in packages if p.id == args.package), None)
        if args.package and not chosen:
            sys.exit(f"沒有 {args.package}；有：{', '.join(p.id for p in packages)}")
        variables = chosen.vars if chosen else cfg.repo_vars()
        width = max(map(len, variables)) + 4
        for key, value in variables.items():
            print(f"{'{{' + key + '}}':<{width}} {value}")
        if not chosen:
            print(f"\n套件：{', '.join(p.id for p in packages)}（加上套件 ID 看套件變數）")


if __name__ == "__main__":
    main()
