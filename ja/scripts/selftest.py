# Copyright 2026 Jeffery Asia
# Licensed under the Apache License, Version 2.0 (see LICENSE at the repository root).
"""ja-jianying 自检：像 Claude 一样通过 MCP 协议启动服务、逐个调用工具，生成一个「JA自检_时间」草稿并核对内容。

只新建草稿，不碰已有草稿；只用 ja/selftest_media 里的合成素材（彩条、测试音），配音要联网（不通时改用备用字幕，记为「记录」）。
用法（用安装时建好的运行环境里的 python）：
    python ja/scripts/selftest.py              默认会试听写（装了听写组件时）
    python ja/scripts/selftest.py --no-asr     跳过听写
结果写到 ja/selftest_report.json；最后几行是「需要人来做的」。
"""
import argparse
import asyncio
import datetime as dt
import json
import os
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

JA = Path(__file__).resolve().parent.parent
MEDIA = JA / "selftest_media"
RESULTS = []


def rec(name, status, detail=""):
    RESULTS.append({"item": name, "status": status, "detail": str(detail)[:500]})
    print("[%s] %s%s" % (status, name, ("：" + str(detail)[:200]) if detail else ""), flush=True)


async def main(args):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(command=sys.executable, args=[str(JA / "server.py")], env=dict(os.environ))
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()

            async def call(tool, **kw):
                res = await s.call_tool(tool, kw)
                text = "".join(getattr(c, "text", "") for c in res.content)
                if res.isError:
                    raise RuntimeError(text)
                try:
                    return json.loads(text)
                except Exception:
                    return text

            tools = sorted(t.name for t in (await s.list_tools()).tools)
            rec("工具列表", "通过" if len(tools) == 11 else "不通过", "%d 个：%s" % (len(tools), ", ".join(tools)))

            st = await call("jy_status")
            ok = bool(st.get("draft_root"))
            rec("找到剪映和草稿目录", "通过" if ok else "不通过",
                "版本 %s（%s）；草稿目录 %s；自动更新 %s；剪映正在运行 %s" % (st.get("version"), st.get("tested"),
                                                         st.get("draft_root"), st.get("auto_update"), st.get("running")))
            if not ok:
                return
            name = "JA自检_" + dt.datetime.now().strftime("%m%d_%H%M%S")
            await call("jy_new_draft", name=name)
            rec("新建草稿", "通过", name)

            await call("jy_add_video", draft=name, path=str(MEDIA / "colorbars_1080x1920_6s.mp4"), volume=0.2)
            rec("放入视频", "通过")

            srt, voice = None, None
            try:
                t = await call("jy_tts", text="剪映自检。字幕和配音由 AI 生成。", name=name + "_tts")
                srt, voice = t["srt"], t["audio"]
                rec("配音和同步字幕（edge-tts）", "通过", "%d 条字幕，人声到 %.1f 秒" % (t["cues"], t["speech_end_s"]))
            except Exception as e:
                rec("配音和同步字幕（edge-tts）", "记录", "没生成（多半是网络）：%s；改用备用字幕和测试音" % e)
                srt, voice = str(MEDIA / "fallback.srt"), str(MEDIA / "tone_6s.mp3")
            a = await call("jy_add_audio", draft=name, path=voice, track="voice", fade_out=0.3)
            rec("放入配音", "通过", "到 %.2f 秒" % a["end"])
            sub = await call("jy_add_subtitles", draft=name, srt_path=srt, y_px=-780, font_size=8)
            rec("放入字幕", "通过", "%d 条，Y %d，字号 %s" % (sub["cues"], sub["y_px"], sub["font_size"]))
            await call("jy_add_text", draft=name, text="自检 " + name[-6:], start=0, duration=6, font_size=12, y_px=700,
                       color="#FFD84D", bold=True)
            rec("放入标题文字", "通过")
            saved = await call("jy_save", draft=name)
            rec("保存草稿", "通过", "%.2f 秒，%s" % (saved["duration_s"], saved["tracks"]))

            # 核对写出来的草稿
            folder = Path(saved["folder"])
            content = json.loads((folder / "draft_content.json").read_text(encoding="utf-8"))
            texts = content["materials"]["texts"]
            subs = [t for t in texts if t.get("type") == "subtitle"]
            sub_segs = next((tr["segments"] for tr in content["tracks"] if tr.get("name") == "subtitle"), [])
            ys = {round(seg["clip"]["transform"]["y"] * 960) for seg in sub_segs}
            bordered = bool(subs) and all(any(st_.get("strokes") for st_ in json.loads(t["content"]).get("styles", []))
                                          for t in subs)
            good = len(subs) == sub["cues"] and ys == {-780} and bordered
            rec("核对草稿内容", "通过" if good else "不通过",
                "字幕素材 %d 条（类型 subtitle）；位置 Y %s；描边 %s" % (len(subs), sorted(ys), "有" if bordered else "没有"))
            meta = json.loads((folder / "draft_meta_info.json").read_text(encoding="utf-8"))
            rec("草稿元数据", "通过" if meta.get("draft_name") == name and meta.get("tm_duration", 0) > 0 else "不通过",
                "名字 %s，时长 %.2f 秒" % (meta.get("draft_name"), meta.get("tm_duration", 0) / 1e6))

            # 安全边界
            try:
                await call("jy_new_draft", name=name)
                rec("不覆盖已有草稿", "不通过", "同名新建没有被拒绝")
            except Exception:
                rec("不覆盖已有草稿", "通过", "同名新建被拒绝")
            try:
                await call("jy_add_text", draft="不存在的草稿_ja", text="x", start=0, duration=1)
                rec("不碰本次会话以外的草稿", "不通过")
            except Exception:
                rec("不碰本次会话以外的草稿", "通过", "被拒绝")

            if not args.no_asr:
                if st.get("components", {}).get("faster_whisper") == "已安装":
                    try:
                        tr = await call("jy_transcribe", media_path=voice, name=name + "_asr", model=args.asr_model)
                        rec("听写（faster-whisper）", "通过" if tr["cues"] > 0 else "不通过",
                            "%d 条，%s，模型 %s" % (tr["cues"], tr["device"], tr["model"]))
                    except Exception as e:
                        rec("听写（faster-whisper）", "不通过", e)
                else:
                    rec("听写（faster-whisper）", "跳过", "没装听写组件（安装时加 --with-asr）")

            print("\n需要人来做的：")
            print("  1. 打开剪映（开着的话回到首页；列表里没有就重开剪映），打开草稿「%s」。" % name)
            print("  2. 看：竖版彩条、顶部黄色标题、底部白字黑边字幕、配音；点一条字幕，右侧应有「字幕」页签。")
            print("  3. 点右上角「导出」，确认能导出。这一步只能人点。")
            print("  4. 自检草稿看完可以在剪映里删掉。")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-asr", action="store_true")
    ap.add_argument("--asr-model", default="small")
    a = ap.parse_args()
    try:
        asyncio.run(main(a))
    except Exception as e:
        rec("自检运行", "不通过", "%s: %s" % (type(e).__name__, e))
    summary = {s: sum(1 for x in RESULTS if x["status"] == s) for s in ("通过", "不通过", "记录", "跳过")}
    (JA / "selftest_report.json").write_text(json.dumps({"time": dt.datetime.now().isoformat(timespec="seconds"),
                                                         "summary": summary, "results": RESULTS},
                                                        ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n===== 自检：通过 %(通过)d，不通过 %(不通过)d，记录 %(记录)d，跳过 %(跳过)d =====" % summary)
    sys.exit(1 if summary["不通过"] else 0)
