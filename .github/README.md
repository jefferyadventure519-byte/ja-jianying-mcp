# ja-jianying · 让 Claude 生成剪映草稿（字幕、配音、文字）

装好以后，你可以在 Claude 里用大白话提需求，比如「给这段竖版视频配上这段文案的配音和字幕」。Claude 会在你的**剪映专业版**里新建一个草稿，把视频、配音、字幕、标题都放好。你打开剪映检查一遍，点导出就行。

本仓库是开源项目 [pyJianYingDraft](https://github.com/GuanYixuan/pyJianYingDraft)（作者 GuanYixuan，Apache-2.0 许可）的 fork。剪映草稿的读写全部来自原项目；我们在 `ja/` 目录里加了一层给 Claude 用的工具，外加安装、自检和操作规则。原项目的说明见根目录的 [README.md](../README.md)。

## 能做什么

| 能做 | 说明 |
|---|---|
| 新建草稿 | 竖版 1080×1920（默认）或横版，名字已存在时拒绝，绝不覆盖 |
| 放视频、图片、音频 | 指定开始时间、截取范围、音量、淡入淡出，可以多条轨叠放 |
| 文案 → 配音 + 字幕 | 用 edge-tts 生成配音，同时生成逐词对齐的字幕，按标点和每行字数自动断句 |
| 人声 → 字幕（可选） | 本机 faster-whisper 听写，不上传 |
| 字幕 | 剪映里识别为「字幕」，可批量改样式；字号、位置 Y 和剪映面板里的数值一致；默认白字黑描边 |
| 标题、人名条 | 普通文字，位置 X / Y 和剪映一致 |

**做不到**：自动导出（剪映 7 起导出按钮对自动化隐藏，要人点）；读取剪映打开过的草稿（剪映会把它加密，本项目不做解密）。

## 先看这一条：剪映版本

| 剪映专业版 | 实测结果（2026-10-20，Windows 11） |
|---|---|
| 11.1.0.14287 | 能打开 AI 写的草稿、字幕识别为「字幕」、导出正常 |
| 11.6.0.14552 | 同上 |
| 其他版本 | 没实测；装完跑一次自检，再在剪映里看一眼 |

剪映开着自动更新时会自己升级（实测一次启动就从 11.1 升到了 11.6）。升级后先让 Claude 跑一次自检。

## 一句话安装

在 Claude Code 里粘贴这句话：

```
请按 https://github.com/jefferyadventure519-byte/ja-jianying-mcp 里 ja/INSTALL_FOR_AI.md 的步骤，帮我安装并测试 ja-jianying
```

Claude 会依次完成：检查环境、下载、安装、注册、自检，最后请你在剪映里看一眼自检草稿。

开始前请准备好：

| 项目 | 说明 |
|---|---|
| Windows 电脑 | 只在 Windows 上验证过 |
| 剪映专业版 | 装好并打开过一次 |
| Python 3.10 或更新 | 推荐 3.11，从 [python.org](https://www.python.org/downloads/) 安装，勾选「Add python.exe to PATH」 |
| 能访问 github.com | 安装要从 GitHub 下载 |
| 联网 | 配音（edge-tts）要联网；听写模型要下载约 460 MB（占约 0.9 GB 磁盘） |

## 装好以后怎么用

1. 在 Claude 的新会话里说你要做什么，比如「把 D:\素材\采访.mp4 做成竖版，配上字幕，字号 8，位置 Y -780」。
2. Claude 保存草稿后，打开剪映（开着的话回首页），打开这个草稿检查。
3. 点右上角「导出」。
4. 要改就让 Claude 新建一版（`_v2`）。剪映打开过的草稿会被加密，Claude 读不回来。

## 几件要知道的事

- 配音用的是 [edge-tts](https://github.com/rany2/edge-tts)，走微软的在线朗读服务（非官方接口、免费、可能随时变）。文案会发到微软；客户未公开的文案先确认能不能发。对客商用前请再评估。
- 听写结果一定要人核对，人名、专有名词最容易错。
- 放进草稿的素材是按文件位置引用的，不要移动或删除，否则剪映里会显示素材丢失。

## 仓库里我们加了什么

| 位置 | 内容 |
|---|---|
| `ja/server.py` | 给 Claude 用的 11 个工具（`jy_` 开头） |
| `ja/jy_core.py` | 找剪映、配音、听写、字幕断句 |
| `ja/INSTALL_FOR_AI.md` | 给 Claude 看的安装步骤 |
| `ja/scripts/install.py` | 安装脚本：先备份再改配置，已有同名注册不擅自覆盖；`--check-only` 只检查、`--uninstall` 卸载 |
| `ja/scripts/selftest.py` | 自检：通过 MCP 调用全部工具，新建「JA自检_…」草稿并核对 |
| `ja/skill/ja-jianying/` | 给 Claude 的操作规则（22 条，都是实测出来的） |
| `ja/selftest_media/` | 自检用的合成素材（彩条、测试音、备用字幕），约 0.8 MB |
| `NOTICE` | 许可与署名说明 |

除 `NOTICE` 和 `.github/README.md` 外，原项目的文件没有改动。

## 卸载

让 Claude 运行 `python ja/scripts/install.py --uninstall`：去掉 Claude 里的注册和规则技能。运行环境在 `%LOCALAPPDATA%\ja-jianying`，生成的配音和字幕在 `文档\ja-jianying`，直接删目录即可。

## 许可与致谢

- 剪映草稿读写：[GuanYixuan/pyJianYingDraft](https://github.com/GuanYixuan/pyJianYingDraft)，Apache License 2.0。
- 本仓库新增部分（`ja/`、`.github/README.md`、`NOTICE`）同样以 Apache License 2.0 发布，见根目录 [LICENSE](../LICENSE) 与 [NOTICE](../NOTICE)。
- 配音：[edge-tts](https://github.com/rany2/edge-tts)（安装时从 PyPI 下载，本仓库不包含其代码）。听写：[faster-whisper](https://github.com/SYSTRAN/faster-whisper)（可选，同上）。
- 「剪映」是其权利人的商标。本项目与剪映官方没有关联。
