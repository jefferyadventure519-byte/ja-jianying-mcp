# Copyright 2026 Jeffery Asia
# Licensed under the Apache License, Version 2.0 (see LICENSE at the repository root).
"""ja-jianying 安装脚本：把「让 Claude 生成剪映草稿」需要的东西装好，并把每一步的结果写成一份报告。

它做的事（每一步都可以单独跳过）：
  1. 检查环境：Windows、Python、剪映的安装位置 / 版本 / 草稿目录、ffmpeg。
  2. 建独立的 Python 运行环境，装依赖（MCP、edge-tts 配音、读素材信息）；加 --with-asr 再装本机听写。
  3. 在 Claude Code 的配置里注册（先备份，原子写入，读回核对；已有同名注册且内容不同时不动它，除非加 --force）。
  4. 把操作规则作为技能装到 ~/.claude/skills/ja-jianying。

用法：
    python ja/scripts/install.py --check-only      只检查环境，不改任何东西
    python ja/scripts/install.py                   完整安装（运行环境建在 %LOCALAPPDATA%\\ja-jianying\\.venv）
    python ja/scripts/install.py --with-asr        同时装听写（faster-whisper，首次会下载约 500 MB 模型）
    python ja/scripts/install.py --uninstall       去掉注册和技能（下载的文件留着，自己删目录即可）
只用 Python 标准库。只在 Windows 上验证过。
"""
import argparse, json, os, shutil, subprocess, sys, tempfile, time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

KIT = Path(__file__).resolve().parent.parent.parent      # 仓库根目录
JA = KIT / "ja"
sys.path.insert(0, str(JA))
import jy_core  # noqa: E402  只用到标准库部分

SERVER_NAME = "jianying"
PIP_MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"
HF_MIRROR = "https://hf-mirror.com"
PKGS = ["mcp[cli]>=1.30,<2", "edge-tts>=7.3,<8", "pymediainfo>=6", "imageio>=2.30",
        "uiautomation>=2; sys_platform == 'win32'"]  # 后三项是 pyJianYingDraft 自己声明的依赖（setup.py）
ASR_PKGS = ["faster-whisper>=1.1,<2"]
REPORT = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "kit": str(KIT), "kit_version": jy_core.KIT_VERSION,
          "steps": [], "blockers": [], "warnings": [], "human_todo": []}


def step(name, ok, detail=""):
    REPORT["steps"].append({"step": name, "ok": ok, "detail": str(detail)[:600]})
    print("[%s] %s%s" % ({True: "完成", False: "失败", None: "跳过"}[ok], name, ("：" + str(detail)[:300]) if detail else ""), flush=True)


def run(cmd, timeout=1800, env=None):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                           stdin=subprocess.DEVNULL, env=env)
        return r.returncode, (r.stdout or ""), (r.stderr or "")
    except Exception as e:
        return 999, "", "%s: %s" % (type(e).__name__, e)


def check_env(args):
    env = {"os": sys.platform, "python": sys.version.split()[0], "python_exe": sys.executable}
    if sys.platform != "win32":
        REPORT["warnings"].append("只在 Windows 上验证过；Mac 版剪映的草稿位置和格式没有测。")
    if sys.version_info < (3, 10):
        REPORT["blockers"].append("运行本脚本的 Python 是 %s，需要 3.10 或更新（推荐 3.11）。请从 python.org 安装后重试。" % env["python"])
    info = jy_core.detect_jianying()
    env["jianying"] = info.as_dict()
    if not info.install_dir:
        REPORT["warnings"].append("没找到剪映专业版。请先从剪映官网（https://www.capcut.cn/）安装并打开一次。")
    elif info.version not in jy_core.TESTED_VERSIONS:
        REPORT["warnings"].append("剪映版本 %s 没有实测过（实测过：%s）。装完一定要跑自检，再在剪映里打开自检草稿看一眼。"
                                  % (info.version, "、".join(jy_core.TESTED_VERSIONS)))
    if not info.draft_root:
        REPORT["warnings"].append("没找到剪映草稿目录。打开一次剪映、新建一个空草稿后再跑。")
    if info.auto_update:
        REPORT["human_todo"].append("剪映开着自动更新，会自己升级到新版本。升级后先让 Claude 跑一次自检，确认新版本还能用。")
    env["ffmpeg"] = jy_core.ffmpeg_path()
    step("检查环境", not REPORT["blockers"], "Python %s；剪映 %s；草稿目录 %s；ffmpeg %s" % (
        env["python"], info.version or "没找到", info.draft_root or "没找到", "有" if env["ffmpeg"] else "没有（不影响）"))
    return env


def make_venv(args):
    venv = Path(args.dir) / ".venv"
    py = venv / "Scripts" / "python.exe"
    if not py.is_file():
        rc, out, err = run([sys.executable, "-m", "venv", str(venv)])
        if rc != 0:
            step("建运行环境", False, err[-300:]); return None
    pkgs = PKGS + (ASR_PKGS if args.with_asr else [])
    for index in ([args.pip_index] if args.pip_index else [None, PIP_MIRROR]):
        cmd = [str(py), "-m", "pip", "install", "--disable-pip-version-check", "-q"] + (["-i", index] if index else []) + pkgs
        rc, out, err = run(cmd)
        if rc == 0:
            rc2, ver, _ = run([str(py), "-c", "import importlib.metadata as m; print(m.version('mcp'), m.version('edge-tts'))"])
            step("建运行环境并装依赖", True, "mcp / edge-tts %s%s" % (ver.strip(), "（用了镜像源）" if index else ""))
            return py
    step("建运行环境并装依赖", False, "pip 安装失败：" + err[-300:]); return None


def prepare_asr(py, args):
    """先把听写模型下载好，免得第一次听写时卡很久。下载不了 huggingface.co 时改用镜像。"""
    code = "from faster_whisper import WhisperModel; WhisperModel(%r, device='cpu', compute_type='int8'); print('ok')" % args.asr_model
    for endpoint in (None, HF_MIRROR):
        env = dict(os.environ)
        if endpoint:
            env["HF_ENDPOINT"] = endpoint
        rc, out, err = run([str(py), "-c", code], timeout=3600, env=env)
        if rc == 0 and "ok" in out:
            step("下载听写模型 " + args.asr_model, True, "用了镜像 " + endpoint if endpoint else "")
            return endpoint
    step("下载听写模型 " + args.asr_model, False, "下载失败，第一次听写时会再试：" + err[-200:])
    return None


def atomic_write_json(path, obj, indent):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=indent)
    os.replace(tmp, path)


def register(py, args, hf_endpoint=None, remove=False):
    cfg = args.claude_config or os.path.join(os.path.expanduser("~"), ".claude.json")
    entry = {"command": str(py), "args": [str(JA / "server.py")], "env": {}}
    if hf_endpoint:
        entry["env"]["HF_ENDPOINT"] = hf_endpoint
    if os.path.isfile(cfg):
        raw = open(cfg, encoding="utf-8").read()
        try:
            j = json.loads(raw)
        except Exception as e:
            step("在 Claude 里注册", False, "%s 不是合法的 JSON，没有改动：%s" % (cfg, e)); return False
        indent = 2 if raw.startswith("{\n  ") else None
    else:
        j, indent = {}, 2
    servers = j.setdefault("mcpServers", {})
    old = servers.get(SERVER_NAME)
    if remove:
        if old is None:
            step("去掉 Claude 里的注册", None, "本来就没有"); return True
        shutil.copy2(cfg, cfg + ".bak_ja_jianying_" + time.strftime("%Y%m%d_%H%M%S"))
        servers.pop(SERVER_NAME); atomic_write_json(cfg, j, indent)
        step("去掉 Claude 里的注册", SERVER_NAME not in json.load(open(cfg, encoding="utf-8")).get("mcpServers", {}), cfg)
        return True
    if old == entry:
        step("在 Claude 里注册", True, "已经是这份配置，没有改动"); return True
    if old is not None and not args.force:
        step("在 Claude 里注册", False, "已经有一个同名的 %s 注册，指向 %s。没有动它；确认要替换的话加 --force 重跑（会先备份）。"
             % (SERVER_NAME, old.get("command"))); return False
    if os.path.isfile(cfg):
        shutil.copy2(cfg, cfg + ".bak_ja_jianying_" + time.strftime("%Y%m%d_%H%M%S"))
    servers[SERVER_NAME] = entry
    atomic_write_json(cfg, j, indent)
    back = json.load(open(cfg, encoding="utf-8")).get("mcpServers", {})
    good = back.get(SERVER_NAME) == entry and len(back) == len(servers)
    step("在 Claude 里注册", good, "%s（其余 %d 个注册原样保留）" % (cfg, len(back) - 1))
    desk = os.path.join(os.environ.get("APPDATA", ""), "Claude", "claude_desktop_config.json")
    try:
        if SERVER_NAME in json.load(open(desk, encoding="utf-8")).get("mcpServers", {}):
            REPORT["warnings"].append("桌面版配置 %s 里也有一个同名注册，两处都在会让同一套工具被加载两次。本脚本没有动它，请只留一处。" % desk)
    except Exception:
        pass
    REPORT["human_todo"].append("完全退出并重开 Claude，新会话里才看得到剪映的工具。")
    return good


def install_skill(args, remove=False):
    dst = Path(args.skills_dir or Path.home() / ".claude" / "skills") / "ja-jianying"
    if remove:
        if dst.is_dir():
            shutil.rmtree(dst); step("去掉技能 ja-jianying", True, dst)
        else:
            step("去掉技能 ja-jianying", None, "本来就没有")
        return
    src = JA / "skill" / "ja-jianying"
    dst.mkdir(parents=True, exist_ok=True)
    n = 0
    for f in src.iterdir():
        shutil.copy2(f, dst / f.name); n += 1
    (dst / "kit_location.txt").write_text(str(KIT) + "\n", encoding="utf-8")
    step("安装技能 ja-jianying（操作规则）", True, "%d 个文件 -> %s" % (n, dst))


def main():
    ap = argparse.ArgumentParser(description="ja-jianying 安装脚本")
    ap.add_argument("--dir", default=os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "ja-jianying"))
    ap.add_argument("--check-only", action="store_true"); ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--force", action="store_true"); ap.add_argument("--with-asr", action="store_true")
    ap.add_argument("--asr-model", default="small"); ap.add_argument("--pip-index", default=None)
    ap.add_argument("--skip-register", action="store_true"); ap.add_argument("--skip-skill", action="store_true")
    ap.add_argument("--claude-config", default=None, help="测试用：改写这个文件而不是 ~/.claude.json")
    ap.add_argument("--skills-dir", default=None, help="测试用：技能装到这个目录而不是 ~/.claude/skills")
    args = ap.parse_args()
    args.dir = os.path.abspath(args.dir)
    REPORT["install_dir"] = args.dir
    env = check_env(args)
    REPORT["env"] = env
    if args.uninstall:
        register(Path(args.dir) / ".venv" / "Scripts" / "python.exe", args, remove=True); install_skill(args, remove=True)
    elif not args.check_only and not REPORT["blockers"]:
        os.makedirs(args.dir, exist_ok=True)
        py = make_venv(args)
        if py:
            hf = prepare_asr(py, args) if args.with_asr else None
            step("在 Claude 里注册", None, "按参数跳过") if args.skip_register else register(py, args, hf)
            step("安装技能", None, "按参数跳过") if args.skip_skill else install_skill(args)
            REPORT["selftest_command"] = '"%s" "%s"%s' % (py, JA / "scripts" / "selftest.py", "" if args.with_asr else " --no-asr")
            REPORT["human_todo"].append("自检后在剪映首页打开「JA自检_…」草稿看一眼，并点一次导出。")
    REPORT["ok"] = (not REPORT["blockers"]) and all(s["ok"] is not False for s in REPORT["steps"])
    for p in ([os.path.join(args.dir, "install_report.json")] if os.path.isdir(args.dir) else []) + [str(JA / "install_report.json")]:
        try:
            json.dump(REPORT, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        except Exception:
            pass
    print("\n===== 结果：%s =====" % ("可以继续" if REPORT["ok"] else "有问题，见下"))
    for k, title in (("blockers", "不能继续的原因"), ("warnings", "提醒"), ("human_todo", "需要人来做的")):
        if REPORT[k]:
            print(title + "：")
            for i, x in enumerate(dict.fromkeys(REPORT[k]), 1):
                print("  %d. %s" % (i, x))
    if REPORT.get("selftest_command"):
        print("自检命令：" + REPORT["selftest_command"])
    return 0 if REPORT["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
