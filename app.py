from __future__ import annotations

import html
import json
import mimetypes
import os
import queue
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


APP_NAME = "网盘聚合搜索下载器"
APP_VERSION = "1.0.0"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
)

PLATFORMS = {
    "全部网盘": [
        "pan.baidu.com",
        "pan.xunlei.com",
        "alipan.com",
        "aliyundrive.com",
        "pan.quark.cn",
        "123pan.com",
        "cloud.189.cn",
        "lanzou",
    ],
    "百度网盘": ["pan.baidu.com"],
    "迅雷云盘": ["pan.xunlei.com"],
    "阿里云盘": ["alipan.com", "aliyundrive.com"],
    "夸克网盘": ["pan.quark.cn"],
    "123云盘": ["123pan.com"],
    "天翼云盘": ["cloud.189.cn"],
    "蓝奏云": ["lanzou"],
}

SHARE_HOST_MARKERS = tuple(
    marker for markers in PLATFORMS.values() for marker in markers
)
DIRECT_EXTENSIONS = {
    ".7z", ".apk", ".avi", ".csv", ".doc", ".docx", ".epub", ".exe",
    ".flac", ".gz", ".iso", ".jpg", ".jpeg", ".m4a", ".mkv", ".mov",
    ".mp3", ".mp4", ".msi", ".pdf", ".png", ".ppt", ".pptx", ".rar",
    ".tar", ".tgz", ".txt", ".wav", ".webm", ".xls", ".xlsx", ".zip",
}


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str = ""
    source: str = ""


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value or "")).strip()


def platform_for_url(url: str) -> str:
    host = urllib.parse.urlparse(url).netloc.lower()
    for label, markers in PLATFORMS.items():
        if label == "全部网盘":
            continue
        if any(marker in host for marker in markers):
            return label
    return "其他来源"


def is_cloud_share_url(url: str) -> bool:
    host = urllib.parse.urlparse(url).netloc.lower()
    return any(marker in host for marker in SHARE_HOST_MARKERS)


def looks_like_direct_file(url: str) -> bool:
    path = urllib.parse.unquote(urllib.parse.urlparse(url).path)
    return Path(path).suffix.lower() in DIRECT_EXTENSIONS


def safe_filename(value: str, fallback: str = "download") -> str:
    value = urllib.parse.unquote(value).strip().strip(".")
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value)
    value = re.sub(r"\s+", " ", value).strip()
    return (value[:180] or fallback)


def filename_from_headers(url: str, headers) -> str:
    disposition = headers.get("Content-Disposition", "")
    match = re.search(r"filename\*=UTF-8''([^;]+)", disposition, re.I)
    if match:
        return safe_filename(match.group(1))
    match = re.search(r'filename="?([^";]+)', disposition, re.I)
    if match:
        return safe_filename(match.group(1))
    name = Path(urllib.parse.unquote(urllib.parse.urlparse(url).path)).name
    if name:
        return safe_filename(name)
    content_type = headers.get_content_type() if hasattr(headers, "get_content_type") else ""
    extension = mimetypes.guess_extension(content_type or "") or ""
    return f"download{extension}"


def _request(url: str, timeout: int = 15) -> urllib.response.addinfourl:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6"},
    )
    return urllib.request.urlopen(request, timeout=timeout)


def search_bing_rss(query: str, domains: list[str], limit: int = 30) -> list[SearchResult]:
    site_query = " OR ".join(f"site:{domain}" for domain in domains)
    full_query = f"{query} ({site_query}) 分享"
    endpoint = "https://www.bing.com/search?format=rss&q=" + urllib.parse.quote(full_query)
    with _request(endpoint) as response:
        root = ET.fromstring(response.read())
    results: list[SearchResult] = []
    for item in root.findall(".//item"):
        url = clean_text(item.findtext("link", ""))
        if not url or not is_cloud_share_url(url):
            continue
        results.append(
            SearchResult(
                title=clean_text(item.findtext("title", "无标题")),
                url=url,
                snippet=clean_text(item.findtext("description", "")),
                source=platform_for_url(url),
            )
        )
        if len(results) >= limit:
            break
    return results


class DuckDuckGoParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.results: list[SearchResult] = []
        self._url = ""
        self._title: list[str] = []
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        classes = values.get("class") or ""
        if tag == "a" and "result__a" in classes:
            href = values.get("href") or ""
            parsed = urllib.parse.urlparse(href)
            if parsed.netloc.endswith("duckduckgo.com"):
                href = urllib.parse.parse_qs(parsed.query).get("uddg", [href])[0]
            self._url = urllib.parse.unquote(href)
            self._title = []
            self._in_title = True

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._in_title:
            self._in_title = False
            if self._url and is_cloud_share_url(self._url):
                self.results.append(
                    SearchResult(clean_text("".join(self._title)), self._url,
                                 source=platform_for_url(self._url))
                )


def search_duckduckgo(query: str, domains: list[str], limit: int = 30) -> list[SearchResult]:
    site_query = " OR ".join(f"site:{domain}" for domain in domains)
    endpoint = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(
        f"{query} ({site_query}) 分享"
    )
    with _request(endpoint) as response:
        content = response.read().decode("utf-8", "replace")
    parser = DuckDuckGoParser()
    parser.feed(content)
    return parser.results[:limit]


def merge_results(*groups: list[SearchResult], limit: int = 50) -> list[SearchResult]:
    seen: set[str] = set()
    merged: list[SearchResult] = []
    for group in groups:
        for item in group:
            normalized = item.url.rstrip("/")
            if normalized in seen:
                continue
            seen.add(normalized)
            merged.append(item)
            if len(merged) >= limit:
                return merged
    return merged


class NetDiskApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"{APP_NAME}  v{APP_VERSION}")
        self.geometry("1040x700")
        self.minsize(820, 560)
        self.configure(bg="#f5f7fb")
        self.events: queue.Queue = queue.Queue()
        self.results: dict[str, SearchResult] = {}
        self.cancel_download = threading.Event()
        self.searching = False
        self.downloading = False
        self._configure_style()
        self._build_ui()
        self.after(100, self._poll_events)

    def _configure_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 19, "bold"), background="#f5f7fb")
        style.configure("Sub.TLabel", font=("Microsoft YaHei UI", 9), foreground="#64748b", background="#f5f7fb")
        style.configure("TButton", font=("Microsoft YaHei UI", 10), padding=(12, 7))
        style.configure("Accent.TButton", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("Treeview", rowheight=31, font=("Microsoft YaHei UI", 9))
        style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 9, "bold"))

    def _build_ui(self) -> None:
        outer = ttk.Frame(self, padding=22)
        outer.pack(fill="both", expand=True)
        header = ttk.Frame(outer)
        header.pack(fill="x", pady=(0, 16))
        ttk.Label(header, text="网盘聚合搜索下载器", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            header,
            text="聚合公开分享页 · 支持公开直链下载 · 不绕过登录、提取码和平台权限",
            style="Sub.TLabel",
        ).pack(anchor="w", pady=(3, 0))

        search_bar = ttk.Frame(outer)
        search_bar.pack(fill="x", pady=(0, 12))
        self.query_var = tk.StringVar()
        entry = ttk.Entry(search_bar, textvariable=self.query_var, font=("Microsoft YaHei UI", 12))
        entry.pack(side="left", fill="x", expand=True, ipady=7)
        entry.bind("<Return>", lambda _event: self.start_search())
        self.platform_var = tk.StringVar(value="全部网盘")
        ttk.Combobox(
            search_bar,
            textvariable=self.platform_var,
            values=list(PLATFORMS),
            state="readonly",
            width=12,
            font=("Microsoft YaHei UI", 10),
        ).pack(side="left", padx=8, ipady=5)
        self.search_button = ttk.Button(search_bar, text="搜索", style="Accent.TButton", command=self.start_search)
        self.search_button.pack(side="left")

        columns = ("source", "title", "url")
        self.tree = ttk.Treeview(outer, columns=columns, show="headings", selectmode="browse")
        self.tree.heading("source", text="来源")
        self.tree.heading("title", text="标题")
        self.tree.heading("url", text="分享链接")
        self.tree.column("source", width=92, minwidth=78, anchor="center", stretch=False)
        self.tree.column("title", width=390, minwidth=220)
        self.tree.column("url", width=460, minwidth=250)
        scroll = ttk.Scrollbar(outer, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="left", fill="y")
        self.tree.bind("<Double-1>", lambda _event: self.open_selected())
        self.tree.bind("<<TreeviewSelect>>", self._show_selected_detail)

        bottom = ttk.Frame(self, padding=(22, 0, 22, 18))
        bottom.pack(fill="x")
        detail = ttk.LabelFrame(bottom, text="选中结果 / 公开直链", padding=10)
        detail.pack(fill="x", pady=(0, 10))
        self.url_var = tk.StringVar()
        ttk.Entry(detail, textvariable=self.url_var).pack(fill="x")
        self.snippet_var = tk.StringVar(value="可粘贴任意 HTTP/HTTPS 公开直链后点击下载。")
        ttk.Label(detail, textvariable=self.snippet_var, foreground="#64748b", wraplength=960).pack(anchor="w", pady=(7, 0))

        actions = ttk.Frame(bottom)
        actions.pack(fill="x")
        ttk.Button(actions, text="浏览器打开", command=self.open_selected).pack(side="left")
        ttk.Button(actions, text="复制链接", command=self.copy_url).pack(side="left", padx=7)
        self.download_button = ttk.Button(actions, text="内置下载", command=self.start_download)
        self.download_button.pack(side="left")
        self.cancel_button = ttk.Button(actions, text="取消下载", command=self.cancel_active_download, state="disabled")
        self.cancel_button.pack(side="left", padx=7)
        self.progress = ttk.Progressbar(actions, mode="determinate", length=210)
        self.progress.pack(side="right")
        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(actions, textvariable=self.status_var).pack(side="right", padx=12)

    def start_search(self) -> None:
        query = self.query_var.get().strip()
        if not query:
            messagebox.showinfo(APP_NAME, "请输入要搜索的内容。")
            return
        if self.searching:
            return
        self.searching = True
        self.search_button.configure(state="disabled")
        self.status_var.set("正在搜索公开分享页…")
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.results.clear()
        domains = PLATFORMS[self.platform_var.get()]
        threading.Thread(target=self._search_worker, args=(query, domains), daemon=True).start()

    def _search_worker(self, query: str, domains: list[str]) -> None:
        groups: list[list[SearchResult]] = []
        errors: list[str] = []
        for name, searcher in (("Bing", search_bing_rss), ("DuckDuckGo", search_duckduckgo)):
            try:
                groups.append(searcher(query, domains))
            except Exception as exc:
                errors.append(f"{name}: {exc}")
        self.events.put(("search_done", merge_results(*groups), errors))

    def _show_selected_detail(self, _event=None) -> None:
        selected = self.tree.selection()
        if not selected:
            return
        result = self.results[selected[0]]
        self.url_var.set(result.url)
        self.snippet_var.set(result.snippet or "双击可在浏览器中打开该分享页。")

    def open_selected(self) -> None:
        url = self.url_var.get().strip()
        if not self._valid_http_url(url):
            messagebox.showinfo(APP_NAME, "请先选择一个结果或粘贴有效链接。")
            return
        webbrowser.open(url)

    def copy_url(self) -> None:
        url = self.url_var.get().strip()
        if not url:
            return
        self.clipboard_clear()
        self.clipboard_append(url)
        self.status_var.set("链接已复制")

    @staticmethod
    def _valid_http_url(url: str) -> bool:
        parsed = urllib.parse.urlparse(url)
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)

    def start_download(self) -> None:
        url = self.url_var.get().strip()
        if not self._valid_http_url(url):
            messagebox.showinfo(APP_NAME, "请先选择一个结果或粘贴有效的 HTTP/HTTPS 链接。")
            return
        if self.downloading:
            messagebox.showinfo(APP_NAME, "已有下载任务正在进行。")
            return
        if is_cloud_share_url(url) and not looks_like_direct_file(url):
            if messagebox.askyesno(
                APP_NAME,
                "这是网盘分享页，通常需要在官方页面登录或输入提取码，无法作为公开直链直接下载。\n\n是否现在打开官方分享页？",
            ):
                webbrowser.open(url)
            return
        suggested = safe_filename(Path(urllib.parse.unquote(urllib.parse.urlparse(url).path)).name)
        target = filedialog.asksaveasfilename(title="保存下载文件", initialfile=suggested)
        if not target:
            return
        self.downloading = True
        self.cancel_download.clear()
        self.download_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self.progress.configure(value=0, maximum=100)
        self.status_var.set("正在连接…")
        threading.Thread(target=self._download_worker, args=(url, Path(target)), daemon=True).start()

    def cancel_active_download(self) -> None:
        self.cancel_download.set()
        self.status_var.set("正在取消…")

    def _download_worker(self, url: str, target: Path) -> None:
        partial = target.with_name(target.name + ".part")
        try:
            existing = partial.stat().st_size if partial.exists() else 0
            headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
            if existing:
                headers["Range"] = f"bytes={existing}-"
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=30) as response:
                content_type = response.headers.get("Content-Type", "").lower()
                disposition = response.headers.get("Content-Disposition", "")
                if "text/html" in content_type and not disposition:
                    raise ValueError("该地址返回的是网页，不是公开文件直链")
                resumed = response.status == 206 and existing > 0
                if existing and not resumed:
                    existing = 0
                mode = "ab" if resumed else "wb"
                content_length = int(response.headers.get("Content-Length", "0") or 0)
                total = existing + content_length if content_length else 0
                downloaded = existing
                started = time.monotonic()
                with partial.open(mode) as output:
                    while True:
                        if self.cancel_download.is_set():
                            self.events.put(("download_cancelled", str(partial)))
                            return
                        chunk = response.read(256 * 1024)
                        if not chunk:
                            break
                        output.write(chunk)
                        downloaded += len(chunk)
                        elapsed = max(time.monotonic() - started, 0.01)
                        speed = (downloaded - existing) / elapsed
                        percent = downloaded * 100 / total if total else -1
                        self.events.put(("download_progress", downloaded, total, speed, percent))
            os.replace(partial, target)
            self.events.put(("download_done", str(target)))
        except Exception as exc:
            self.events.put(("download_error", str(exc), str(partial)))

    @staticmethod
    def _human_size(value: float) -> str:
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if value < 1024 or unit == "TB":
                return f"{value:.1f} {unit}"
            value /= 1024
        return ""

    def _poll_events(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                kind = event[0]
                if kind == "search_done":
                    _, results, errors = event
                    self.searching = False
                    self.search_button.configure(state="normal")
                    for result in results:
                        iid = self.tree.insert("", "end", values=(result.source, result.title, result.url))
                        self.results[iid] = result
                    if results:
                        self.status_var.set(f"找到 {len(results)} 条公开分享结果")
                    elif errors:
                        self.status_var.set("搜索服务暂时不可用")
                        messagebox.showwarning(APP_NAME, "暂未取得结果。请检查网络后重试。\n\n" + "\n".join(errors))
                    else:
                        self.status_var.set("没有找到公开分享结果")
                elif kind == "download_progress":
                    _, done, total, speed, percent = event
                    if percent >= 0:
                        self.progress.configure(mode="determinate", value=percent)
                        total_text = f" / {self._human_size(total)}"
                    else:
                        self.progress.configure(mode="indeterminate")
                        self.progress.start(12)
                        total_text = ""
                    self.status_var.set(
                        f"{self._human_size(done)}{total_text} · {self._human_size(speed)}/s"
                    )
                elif kind in {"download_done", "download_cancelled", "download_error"}:
                    self.downloading = False
                    self.progress.stop()
                    self.download_button.configure(state="normal")
                    self.cancel_button.configure(state="disabled")
                    if kind == "download_done":
                        path = event[1]
                        self.progress.configure(mode="determinate", value=100)
                        self.status_var.set("下载完成")
                        if messagebox.askyesno(APP_NAME, "下载完成。是否打开文件所在文件夹？"):
                            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
                    elif kind == "download_cancelled":
                        self.status_var.set("下载已取消，可稍后续传")
                    else:
                        self.status_var.set("下载失败")
                        messagebox.showerror(
                            APP_NAME,
                            f"下载失败：{event[1]}\n\n未完成的数据保留为 .part 文件，可用同一路径再次尝试续传。",
                        )
        except queue.Empty:
            pass
        self.after(100, self._poll_events)


def main() -> None:
    app = NetDiskApp()
    app.mainloop()


if __name__ == "__main__":
    main()

