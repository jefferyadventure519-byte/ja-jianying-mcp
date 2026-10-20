# Copyright 2026 Jeffery Asia
# Licensed under the Apache License, Version 2.0 (see LICENSE at the repository root).
"""ja-jianying MCP 服务：让 Claude 生成剪映草稿（视频、配音、字幕、文字），人打开剪映检查后导出。

边界（工具本身强制）：
- 只新建草稿，绝不覆盖已有草稿（剪映打开或改过的草稿是加密的，覆盖会冲掉人的修改）。
- 只能编辑本次会话里新建的草稿；改版就新建一个名字带 _v2 的草稿。
- 不读、不解密剪映加密的草稿。
- 不自动导出：剪映 7 以后导出按钮对自动化隐藏，导出由人在剪映里点。
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))        # 用本仓库（fork）里的 pyJianYingDraft，而不是别处装的版本
sys.path.insert(0, str(REPO / "ja"))

import pyJianYingDraft as jy                      # noqa: E402
from pyJianYingDraft import (ClipSettings, TextBorder, TextStyle, TrackSpec,  # noqa: E402
                             TrackType, trange_seconds)
from mcp.server.fastmcp import FastMCP            # noqa: E402

import jy_core as core                           # noqa: E402

mcp = FastMCP("jianying", log_level="WARNING")
_DRAFTS: dict = {}   # 本次会话新建的草稿：name -> {"script", "dir", "w", "h"}


def _media_dir(sub: str) -> Path:
    base = os.environ.get("JA_JIANYING_MEDIA") or str(Path.home() / "Documents" / "ja-jianying")
    d = Path(base) / _dt.date.today().strftime("%Y%m%d") / sub
    d.mkdir(parents=True, exist_ok=True)
    return d


def _get(draft: str) -> dict:
    if draft not in _DRAFTS:
        raise ValueError("「%s」不是本次会话里新建的草稿。为了不碰到人改过的草稿，只能编辑本次用 jy_new_draft 建的草稿；"
                         "要改版请新建一个（比如加 _v2）。" % draft)
    return _DRAFTS[draft]


def _ensure_track(script, kind: TrackType, name: str) -> None:
    if name not in script.tracks:
        script.append_track(TrackSpec(kind, name))


def _file(path: str) -> str:
    p = Path(path).expanduser()
    if not p.is_file():
        raise FileNotFoundError("找不到文件：%s" % path)
    return str(p.resolve())


@mcp.tool()
def jy_status() -> dict:
    """检查这台电脑的剪映：安装位置、版本、是否实测过、草稿目录、是否开着自动更新、是否正在运行；以及配音、听写组件是否可用。开工前先调一次。"""
    info = core.detect_jianying().as_dict()
    comp = {}
    for mod in ("edge_tts", "faster_whisper"):  # 只查装没装，不导入（导入听写库会卡住服务，见 jy_core 末尾说明）
        comp[mod] = "已安装" if importlib.util.find_spec(mod) else "没装"
    info.update({"components": comp, "ffmpeg": core.ffmpeg_path() or "没找到（不影响生成草稿）",
                 "kit_version": core.KIT_VERSION, "upstream": core.UPSTREAM_TESTED,
                 "reminders": ["导出要人在剪映里点。", "剪映打开过的草稿会被加密，AI 读不回来；改版就新建草稿。",
                               "开着自动更新时剪映会自己升级；新版本没实测过的话先跑一次自检。"]})
    return info


@mcp.tool()
def jy_list_drafts() -> list:
    """列出剪映草稿目录里的草稿：名字、修改时间、状态（plain=AI 可读 / encrypted=剪映已加密 / missing=不是草稿）。"""
    root = core.detect_jianying().draft_root
    if not root:
        raise RuntimeError("没找到剪映草稿目录。请先打开一次剪映。")
    out = []
    for d in sorted(Path(root).iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if d.is_dir() and not d.name.startswith("."):
            out.append({"name": d.name, "state": core.draft_state(d),
                        "modified": _dt.datetime.fromtimestamp(d.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
                        "this_session": d.name in _DRAFTS})
    return out


@mcp.tool()
def jy_new_draft(name: str, width: int = 1080, height: int = 1920, fps: int = 30) -> dict:
    """新建一个剪映草稿。竖版 1080x1920（默认），横版传 1920x1080。名字已存在时拒绝，绝不覆盖。"""
    root = core.detect_jianying().draft_root
    if not root:
        raise RuntimeError("没找到剪映草稿目录。请先打开一次剪映。")
    if (Path(root) / name).exists():
        raise FileExistsError("剪映里已经有叫「%s」的草稿，不会覆盖。请换个名字（比如加 _v2）。" % name)
    script = jy.DraftFolder(root).create_draft(name, width, height, fps)
    _DRAFTS[name] = {"script": script, "dir": Path(root) / name, "w": width, "h": height}
    return {"draft": name, "folder": str(Path(root) / name), "size": "%dx%d" % (width, height), "fps": fps}


@mcp.tool()
def jy_add_video(draft: str, path: str, start: float = 0.0, duration: Optional[float] = None,
                 source_start: float = 0.0, volume: float = 1.0, track: str = "video") -> dict:
    """往草稿里放一段视频或图片。start=在时间线上的开始秒数；duration=放多长（视频默认用到素材结尾，图片必须给）；
    source_start=从素材第几秒开始截；volume=原声音量（0 静音，1 原样）；track=轨道名，不同名字会新建轨道（叠画面用）。"""
    d = _get(draft)
    mat = jy.VideoMaterial(_file(path))
    if mat.material_type == "photo" and duration is None:
        raise ValueError("图片要给 duration（秒）。")
    total = mat.duration / 1e6
    dur = duration if duration is not None else max(0.0, total - source_start)
    if mat.material_type != "photo" and source_start + dur > total + 1e-3:
        raise ValueError("素材只有 %.2f 秒，从 %.2f 秒开始放不下 %.2f 秒。" % (total, source_start, dur))
    _ensure_track(d["script"], TrackType.video, track)
    seg = jy.VideoSegment(mat, trange_seconds(start, duration=dur),
                          source_timerange=None if mat.material_type == "photo" else trange_seconds(source_start, duration=dur),
                          volume=volume)
    d["script"].add_segment(seg, track)
    return {"track": track, "start": start, "end": round(start + dur, 3), "type": mat.material_type}


@mcp.tool()
def jy_add_audio(draft: str, path: str, start: float = 0.0, duration: Optional[float] = None,
                 source_start: float = 0.0, volume: float = 1.0, fade_in: float = 0.0, fade_out: float = 0.0,
                 track: str = "audio") -> dict:
    """往草稿里放一段音频（配音、音乐、音效）。volume 1=原样；fade_in/fade_out 秒；不同 track 名会新建音频轨。"""
    d = _get(draft)
    mat = jy.AudioMaterial(_file(path))
    total = mat.duration / 1e6
    dur = duration if duration is not None else max(0.0, total - source_start)
    if source_start + dur > total + 1e-3:
        raise ValueError("音频只有 %.2f 秒，从 %.2f 秒开始放不下 %.2f 秒。" % (total, source_start, dur))
    _ensure_track(d["script"], TrackType.audio, track)
    seg = jy.AudioSegment(mat, trange_seconds(start, duration=dur),
                          source_timerange=trange_seconds(source_start, duration=dur), volume=volume)
    if fade_in or fade_out:
        seg.add_fade(int(fade_in * 1e6), int(fade_out * 1e6))
    d["script"].add_segment(seg, track)
    return {"track": track, "start": start, "end": round(start + dur, 3)}


@mcp.tool()
async def jy_tts(text: str, name: Optional[str] = None, voice: str = "zh-CN-XiaoxiaoNeural", rate: str = "+0%",
           max_chars: int = 16) -> dict:
    """把文案变成配音（mp3）和对得上的字幕（srt）。字幕按标点和每行最多 max_chars 个字自动断开。
    用 edge-tts（免费、走微软非官方接口、需要联网）；对客商用前要再评估。voice 可用 jy_voices 查。"""
    name = name or "tts_" + _dt.datetime.now().strftime("%H%M%S")
    return await core.tts_async(text, _media_dir("tts"), name, voice=voice, rate=rate, max_chars=max_chars)


@mcp.tool()
async def jy_voices(locale: str = "zh-") -> list:
    """列出可用的配音音色。locale 例：zh-（全部中文）、zh-CN、en-US。"""
    return await core.list_voices_async(locale)


@mcp.tool()
async def jy_transcribe(media_path: str, name: Optional[str] = None, language: str = "zh", model: str = "small",
                  max_chars: int = 16, hint: Optional[str] = None) -> dict:
    """把视频或音频里的人声听写成字幕（srt），本机运行，不上传。需要安装时加了 --with-asr。
    hint 写上片子里的人名、专有名词能提高准确率。结果一定要人核对。"""
    name = name or Path(media_path).stem + "_asr"
    if not importlib.util.find_spec("faster_whisper"):
        raise RuntimeError("没有装听写组件。请让 Claude 重跑安装脚本并加 --with-asr。")
    payload = {"media": _file(media_path), "out_dir": str(_media_dir("asr")), "name": name, "language": language,
               "model": model, "max_chars": max_chars, "initial_prompt": hint}
    r = await asyncio.to_thread(subprocess.run, [sys.executable, str(REPO / "ja" / "jy_core.py"), "transcribe",
                                                 json.dumps(payload, ensure_ascii=False)],
                                stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=3600)
    line = next((x for x in r.stdout.splitlines() if x.startswith("JA_RESULT ")), None)
    if r.returncode != 0 or not line:
        raise RuntimeError("听写失败：" + (r.stderr or r.stdout)[-600:])
    return json.loads(line[len("JA_RESULT "):])


@mcp.tool()
def jy_add_subtitles(draft: str, srt_path: str, offset: float = 0.0, font_size: float = 8.0,
                     y_px: Optional[float] = None, color: str = "#FFFFFF", bold: bool = False,
                     border: bool = True, border_color: str = "#000000", border_width: float = 40.0,
                     max_line_width: float = 0.82, track: str = "subtitle") -> dict:
    """把 srt 字幕放进草稿，剪映里显示为「字幕」（可以批量改样式）。
    font_size 与剪映「字号」一致；y_px 与剪映「位置 Y」一致（负数往下，竖版常用 -780；不填用剪映导入字幕的默认位置）；
    默认白字黑描边。offset=整体往后挪的秒数。"""
    d = _get(draft)
    srt = Path(_file(srt_path))
    cues = core.read_srt(srt)
    fixed = _media_dir("srt") / (srt.stem + "_ja.srt")
    core.write_srt(cues, fixed)  # 去掉重叠后的副本写到 文档\ja-jianying，原文件和它所在的文件夹都不动
    half_h = d["h"] / 2
    ty = (y_px / half_h) if y_px is not None else -0.8
    ref = jy.TextSegment("参考", trange_seconds(0, duration=1),
                         style=TextStyle(size=font_size, bold=bold, color=core.hex_to_rgb(color), align=1,
                                         auto_wrapping=True, max_line_width=max_line_width),
                         border=TextBorder(color=core.hex_to_rgb(border_color), width=border_width) if border else None)
    d["script"].import_srt(str(fixed), track, time_offset=int(offset * 1e6), style_reference=ref,
                           clip_settings=ClipSettings(transform_y=ty))
    return {"track": track, "cues": len(cues), "y_px": round(ty * half_h), "font_size": font_size}


@mcp.tool()
def jy_add_text(draft: str, text: str, start: float, duration: float, font_size: float = 12.0,
                x_px: float = 0.0, y_px: float = 0.0, color: str = "#FFFFFF", bold: bool = False,
                border: bool = True, border_color: str = "#000000", track: str = "text") -> dict:
    """放一段普通文字（标题、人名条等，不算字幕）。x_px / y_px 与剪映「位置 X / Y」一致（0,0 是画面中心，Y 负数往下）。"""
    d = _get(draft)
    _ensure_track(d["script"], TrackType.text, track)
    seg = jy.TextSegment(text, trange_seconds(start, duration=duration),
                         style=TextStyle(size=font_size, bold=bold, color=core.hex_to_rgb(color), align=1),
                         border=TextBorder(color=core.hex_to_rgb(border_color)) if border else None,
                         clip_settings=ClipSettings(transform_x=x_px / (d["w"] / 2), transform_y=y_px / (d["h"] / 2)))
    d["script"].add_segment(seg, track)
    return {"track": track, "start": start, "end": round(start + duration, 3)}


@mcp.tool()
def jy_save(draft: str) -> dict:
    """保存草稿。保存后请人在剪映首页打开它检查、导出（剪映开着的话先回首页，看不到就重开剪映）。"""
    d = _get(draft)
    d["script"].save()
    core.fresh_meta(d["dir"], d["script"].duration)
    return {"draft": draft, "folder": str(d["dir"]), "duration_s": round(d["script"].duration / 1e6, 3),
            "tracks": [(t.track_type.name, n, len(t.segments)) for n, t in d["script"].tracks.items()],
            "next": ["在剪映首页打开这个草稿（开着的话回首页；列表里没有就重开剪映）。",
                     "检查画面、字幕位置、配音，然后点右上角「导出」。",
                     "剪映打开后草稿会加密，AI 读不回来；要改版请让 Claude 新建一个 _v2。"]}


def main():
    mcp.run()


if __name__ == "__main__":
    main()
