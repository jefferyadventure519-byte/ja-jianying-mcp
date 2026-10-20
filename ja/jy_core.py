# Copyright 2026 Jeffery Asia
# Licensed under the Apache License, Version 2.0 (see LICENSE at the repository root).
"""ja-jianying 的底层功能：找剪映、配音、听写、字幕断句、草稿安全检查。

不依赖 MCP，可以单独 import 测试。剪映草稿的读写本身交给本仓库的 pyJianYingDraft。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

KIT_VERSION = "0.1.0"

# 在这些剪映版本上实测过：能打开 AI 写的草稿、字幕被识别为「字幕」、能导出（人点）。
TESTED_VERSIONS = {
    "11.1.0.14287": "2026-10-20 实测通过",
    "11.6.0.14552": "2026-10-20 实测通过",
}
UPSTREAM_TESTED = "上游 pyJianYingDraft 作者实测 5.9 与 10.8"

PUNCT = "，。！？；：、,.!?;:…—"
BREAK_PUNCT = "。！？；!?;"  # 句末标点：一定断开


# ---------------------------------------------------------------- 剪映环境

@dataclass
class JianyingInfo:
    install_dir: Optional[str]
    version: Optional[str]
    draft_root: Optional[str]
    draft_root_source: str
    auto_update: Optional[bool]
    running: bool

    def as_dict(self):
        d = self.__dict__.copy()
        d["tested"] = TESTED_VERSIONS.get(self.version or "", "没有实测过这个版本")
        return d


def _reg_uninstall_dir() -> Optional[str]:
    if sys.platform != "win32":
        return None
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall\JianyingPro")
        cmd, _ = winreg.QueryValueEx(k, "UninstallString")
        return os.path.dirname(cmd.strip().strip('"'))
    except OSError:
        return None


def _user_data_dir() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "JianyingPro" / "User Data"


def _read_ini_value(path: Path, key: str) -> Optional[str]:
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith(key + "="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return None


def detect_jianying() -> JianyingInfo:
    install_dir = _reg_uninstall_dir()
    version = None
    if install_dir:
        xml = Path(install_dir) / "JianyingProPacket.xml"
        try:
            m = re.search(r'full_appver value="([\d.]+)"', xml.read_text(encoding="utf-8", errors="replace"))
            version = m.group(1) if m else None
        except OSError:
            pass
    setting = _user_data_dir() / "Config" / "globalSetting"
    custom = _read_ini_value(setting, "currentCustomDraftPath")
    auto = _read_ini_value(setting, "enableAutoUpdate")
    if custom:
        root, src = custom.replace("\\\\", "\\"), "剪映设置里的草稿位置"
    else:
        root, src = str(_user_data_dir() / "Projects" / "com.lveditor.draft"), "剪映默认草稿位置"
    if not os.path.isdir(root):
        root, src = None, "没找到草稿目录（剪映装了吗？打开过一次吗？）"
    running = False
    if sys.platform == "win32":
        try:
            out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq JianyingPro.exe", "/NH"],
                                 capture_output=True, text=True, timeout=15).stdout
            running = "JianyingPro.exe" in out
        except Exception:
            pass
    return JianyingInfo(install_dir, version, root, src,
                        None if auto is None else auto.lower() == "true", running)


def draft_state(folder: Path) -> str:
    """plain：明文（AI 能读）；encrypted：剪映打开或改过后加密了；missing：不是草稿。"""
    p = folder / "draft_content.json"
    if not p.is_file():
        return "missing"
    try:
        json.loads(p.read_bytes())
        return "plain"
    except Exception:
        return "encrypted"


# ---------------------------------------------------------------- 字幕断句

@dataclass
class Cue:
    start: float  # 秒
    end: float
    text: str


def _clean_line(s: str) -> str:
    s = s.strip()
    s = re.sub(r"[，、,；;：:]+", " ", s)  # 行内逗号换空格
    s = s.strip(" " + PUNCT)                # 行尾标点去掉
    return re.sub(r"\s+", " ", s)


def words_to_cues(text: str, words: List[Tuple[float, float, str]], max_chars: int = 16) -> List[Cue]:
    """把逐词时间（开始秒, 结束秒, 词）按标点和每行字数组成字幕。

    词在原文里按顺序定位，词与词之间夹着的标点决定断句：句末标点一定断，逗号类在接近上限时断。
    """
    cues: List[Cue] = []
    pos = 0
    cur_text, cur_start, cur_end = "", None, None

    def flush():
        nonlocal cur_text, cur_start, cur_end
        line = _clean_line(cur_text)
        if line and cur_start is not None:
            cues.append(Cue(cur_start, cur_end, line))
        cur_text, cur_start, cur_end = "", None, None

    for i, (ws, we, w) in enumerate(words):
        idx = text.find(w, pos)
        gap = text[pos:idx] if idx >= 0 else ""
        if idx < 0:
            idx = pos
        sep = gap + (cur_text[-1] if cur_text else "")  # 听写的标点粘在上一个词后面，配音的标点夹在词之间
        if cur_text and any(c in BREAK_PUNCT for c in sep):
            flush()
        elif cur_text and any(c in PUNCT for c in sep) and len(_clean_line(cur_text)) >= max_chars * 0.5:
            flush()
        elif cur_text and len(_clean_line(cur_text + gap + w)) > max_chars:
            flush()
        cur_text += (gap if cur_text else "") + w
        cur_start = ws if cur_start is None else cur_start
        cur_end = we
        pos = idx + len(w)
    flush()
    for a, b in zip(cues, cues[1:]):  # 同一轨道不能重叠
        if a.end > b.start:
            a.end = b.start
    return cues


def _ts(sec: float) -> str:
    ms = max(0, int(round(sec * 1000)))
    return "%02d:%02d:%02d,%03d" % (ms // 3600000, ms // 60000 % 60, ms // 1000 % 60, ms % 1000)


def write_srt(cues: List[Cue], path: Path) -> Path:
    path.write_text("\n".join("%d\n%s --> %s\n%s\n" % (i, _ts(c.start), _ts(c.end), c.text)
                              for i, c in enumerate(cues, 1)), encoding="utf-8")
    return path


def read_srt(path: Path) -> List[Cue]:
    def sec(t):
        h, m, rest = t.strip().split(":")
        s, ms = rest.split(",")
        return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000
    cues = []
    for block in re.split(r"\n\s*\n", path.read_text(encoding="utf-8-sig").strip()):
        lines = [x for x in block.strip().splitlines() if x.strip()]
        if len(lines) >= 3 and "-->" in lines[1]:
            a, b = lines[1].split("-->")
            cues.append(Cue(sec(a), sec(b), "\n".join(lines[2:])))
    for a, b in zip(cues, cues[1:]):
        if a.end > b.start:
            a.end = b.start
    return cues


# ---------------------------------------------------------------- 配音（edge-tts）

async def tts_async(text: str, out_dir: Path, name: str, voice: str = "zh-CN-XiaoxiaoNeural",
                    rate: str = "+0%", volume: str = "+0%", max_chars: int = 16) -> dict:
    import edge_tts  # 延迟导入：没装时其余功能照常

    out_dir.mkdir(parents=True, exist_ok=True)
    mp3, srt = out_dir / (name + ".mp3"), out_dir / (name + ".srt")
    words: List[Tuple[float, float, str]] = []
    comm = edge_tts.Communicate(text, voice, rate=rate, volume=volume, boundary="WordBoundary")
    with open(mp3, "wb") as f:
        async for ch in comm.stream():
            if ch["type"] == "audio":
                f.write(ch["data"])
            elif ch["type"] == "WordBoundary":
                words.append((ch["offset"] / 1e7, (ch["offset"] + ch["duration"]) / 1e7, ch["text"]))
    cues = words_to_cues(text, words, max_chars)
    write_srt(cues, srt)
    return {"audio": str(mp3), "srt": str(srt), "voice": voice, "cues": len(cues),
            "speech_end_s": round(words[-1][1], 3) if words else 0}


def tts(*args, **kwargs) -> dict:
    """同步版，单独脚本里用；MCP 服务里用 tts_async。"""
    return asyncio.run(tts_async(*args, **kwargs))


async def list_voices_async(locale_prefix: str = "zh-") -> List[dict]:
    import edge_tts

    voices = await edge_tts.list_voices()
    return [{"name": v["ShortName"], "gender": v["Gender"], "locale": v["Locale"]}
            for v in voices if v["Locale"].lower().startswith(locale_prefix.lower())]


# ---------------------------------------------------------------- 听写（faster-whisper，可选）

def transcribe(media: Path, out_dir: Path, name: str, language: str = "zh",
               model: str = "small", max_chars: int = 16, initial_prompt: Optional[str] = None) -> dict:
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise RuntimeError("没有装听写组件。请让 Claude 重跑安装脚本并加 --with-asr。")
    device, compute = "cpu", "int8"
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() > 0:
            device, compute = "cuda", "float16"
    except Exception:
        pass
    def run(dev, ct):
        wm = WhisperModel(model, device=dev, compute_type=ct)
        segs, info = wm.transcribe(str(media), language=language, word_timestamps=True, vad_filter=True,
                                   initial_prompt=initial_prompt)
        return list(segs), info

    try:
        segs, info = run(device, compute)
    except Exception:
        if device == "cpu":
            raise
        device, compute = "cpu", "int8"  # 显卡驱动或 CUDA 库不全时退回 CPU
        segs, info = run(device, compute)
    cues: List[Cue] = []
    for s in segs:
        ws = [(w.start, w.end, w.word.strip()) for w in (s.words or []) if w.word.strip()]
        if ws:
            cues.extend(words_to_cues("".join(w[2] for w in ws), ws, max_chars))
    for a, b in zip(cues, cues[1:]):
        if a.end > b.start:
            a.end = b.start
    out_dir.mkdir(parents=True, exist_ok=True)
    srt = write_srt(cues, out_dir / (name + ".srt"))
    return {"srt": str(srt), "cues": len(cues), "language": info.language, "device": device, "model": model,
            "note": "听写结果要人核对：人名、专有名词、英文缩写最容易错。"}


# ---------------------------------------------------------------- 杂项

def hex_to_rgb(color: str) -> Tuple[float, float, float]:
    c = color.lstrip("#")
    if len(c) != 6:
        raise ValueError("颜色要写成 #RRGGBB，比如 #FFFFFF")
    return tuple(int(c[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def ffmpeg_path() -> Optional[str]:
    return shutil.which("ffmpeg")


def fresh_meta(draft_dir: Path, duration_us: int) -> None:
    """补全草稿元数据（名字、位置、时长、新 ID），让剪映首页列表显示正确的时长。"""
    p = draft_dir / "draft_meta_info.json"
    try:
        m = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return
    m.update({"draft_id": str(uuid.uuid4()).upper(), "draft_name": draft_dir.name,
              "draft_fold_path": str(draft_dir).replace("\\", "/"),
              "draft_root_path": str(draft_dir.parent).replace("\\", "/"),
              "tm_duration": int(duration_us)})
    p.write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    # 听写在独立进程里跑（stdin 接空）：在 MCP 服务进程里直接导入听写库，Windows 上会和协议通道抢 stdin 而卡死。
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    if len(sys.argv) == 3 and sys.argv[1] == "transcribe":
        a = json.loads(sys.argv[2])
        r = transcribe(Path(a["media"]), Path(a["out_dir"]), a["name"], language=a.get("language", "zh"),
                       model=a.get("model", "small"), max_chars=a.get("max_chars", 16),
                       initial_prompt=a.get("initial_prompt"))
        print("JA_RESULT " + json.dumps(r, ensure_ascii=False))
