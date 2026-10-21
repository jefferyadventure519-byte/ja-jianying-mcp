# 给 AI 的安装指令（ja-jianying）

你（Claude）正在帮一位同事把「用 Claude 生成剪映草稿」装到他的电脑上。请严格按下面的顺序做。容易出错的环节都写成了脚本，你负责运行脚本、读结果、用大白话和使用者沟通。

## 边界（先读）

- 只让使用者做「点击」类的事（装软件、打开剪映、点导出、重开程序），**不要让他敲命令**。命令都由你来跑。
- **命令一律用 PowerShell 执行**，照抄下面的写法：路径用 `$env:LOCALAPPDATA\…` 写成绝对路径，不要用 cmd 的 `%LOCALAPPDATA%`，也不要先进入某个目录再跑相对路径（Claude Code 每条命令跑完会回到原来的目录）。
- 不要动 Claude 配置里别的注册项，不要动剪映的安装和设置。
- 自检只会**新建**一个名字以「JA自检_」开头的草稿，不会碰使用者已有的草稿。
- 任何一步失败，先把脚本输出的原因用一两句话告诉使用者，再决定下一步；不要反复重试同一条命令。
- 只在 Windows 上验证过。使用者是 Mac 时，先说明「没有验证过」，由他决定要不要继续。

## 第 1 步：确认前提

1. **剪映专业版**已经装好，并且打开过一次（这样才有草稿目录）。这一条不用现在核实，第 3 步的脚本会查。实测过的版本是 11.1.0.14287 和 11.6.0.14552，其他版本第 3 步会提醒。
2. **Python**：运行 `python --version`（不行再试 `py -3 --version`），需要 3.10 或更新，推荐 3.11。没有的话，请使用者去 python.org 下载 3.11 的 Windows 安装包，安装时勾上「Add python.exe to PATH」，装完告诉你。只有 `py -3` 能用时，下面命令里的 `python` 都换成 `py -3`。
3. 问使用者要不要**听写**（把片子里的人声转成字幕）。要的话第 4 步加 `--with-asr`，会多下载约 460 MB（Windows 上占约 0.9 GB 磁盘）。
4. **C 盘剩余空间**：运行环境和听写模型都装在 C 盘。用下面这条看还剩多少 GB；剩余不到 2 GB 时，先告诉使用者，请他清理出空间再继续。

   ```powershell
   [math]::Round((Get-PSDrive C).Free / 1GB, 1)
   ```

## 第 2 步：下载本仓库

目标位置：`$env:LOCALAPPDATA\ja-jianying\kit`。

有 git 时（`git --version` 能显示版本号）：

```powershell
$d = "$env:LOCALAPPDATA\ja-jianying\kit"
if (Test-Path "$d\.git") { git -C $d pull --ff-only } else { git clone https://github.com/jefferyadventure519-byte/ja-jianying-mcp.git $d }
```

没有 git 时，下载 zip：

```powershell
$d = "$env:LOCALAPPDATA\ja-jianying\kit"; $z = "$env:TEMP\ja-jianying-mcp.zip"; $x = "$env:TEMP\ja-jianying-mcp-x"
Invoke-WebRequest "https://github.com/jefferyadventure519-byte/ja-jianying-mcp/archive/refs/heads/main.zip" -OutFile $z -UseBasicParsing
if (Test-Path $x) { Remove-Item $x -Recurse -Force }
Expand-Archive $z $x -Force
if (Test-Path $d) { Remove-Item $d -Recurse -Force }
New-Item -ItemType Directory -Force (Split-Path $d) | Out-Null
Move-Item "$x\ja-jianying-mcp-main" $d
```

下载失败多半是访问不了 github.com，请如实告诉使用者，由他解决网络后再继续。

## 第 3 步：只检查，不改动

```powershell
python "$env:LOCALAPPDATA\ja-jianying\kit\ja\scripts\install.py" --check-only
```

读输出最后的「结果」一段，里面最多有三类：

- 「不能继续的原因」：停下，把原因讲给使用者听。
- 「提醒」：讲清楚，问使用者要不要继续。最常见的是「剪映版本没有实测过」——可以继续，装完靠自检确认。如果提醒的是「没找到剪映专业版」或「没找到剪映草稿目录」，先请使用者装好剪映、打开一次，再重跑本步。
- 「需要人来做的」：不是问题，转告使用者就行，不用停下来问。这一步最常见的是「剪映开着自动更新」。

## 第 4 步：安装

```powershell
python "$env:LOCALAPPDATA\ja-jianying\kit\ja\scripts\install.py"
```

要听写就在末尾加 ` --with-asr`。

**这条命令可能要跑几分钟**：本机已有下载缓存时实测 1–2 分钟；全新电脑要现下载依赖，带听写还要下载约 460 MB 的模型，会更久。Claude Code 前台命令默认 2 分钟就会被中止，所以请放到后台运行、等它结束再读输出；不放后台的话，把这条命令的超时设到最大（10 分钟）。

它会建运行环境、装依赖、在 Claude 里注册（名字 `jianying`）、装上规则技能。逐条读「完成 / 失败 / 跳过」：

- 「在 Claude 里注册」失败并提示已有同名注册：说明这台电脑以前装过。把旧注册指向的位置告诉使用者，问他是否替换；同意后加 `--force` 重跑。
- 提示桌面版配置里也有同名注册：告诉使用者两处同时存在会重复加载，建议只留一处，由他决定，不要自己去删。
- pip 下载失败：脚本会自动换清华镜像再试一次；还不行就如实说网络问题。
- 结果里「需要人来做的」（重开 Claude、在剪映里看自检草稿）不用现在停下来，第 6、7 步再请使用者做。

## 第 5 步：自检

安装输出的最后一行有「自检命令」，原样运行。在 PowerShell 里要在最前面加 `&`，写成 `& "…\python.exe" "…\selftest.py"`（没装听写时末尾还有 `--no-asr`，照抄），不加会报「表达式或语句中存在意外的标记」，命令不会运行。

从桌面版 Claude 安装时，命令里的路径可能有一段是 `…\AppData\Local\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Local\ja-jianying\…`。这是桌面版把 `%LOCALAPPDATA%` 下新建的目录放进了它自己的私有目录，不是装错了，照常运行。

自检会像你以后一样，通过 MCP 调用每个工具：新建「JA自检_…」草稿，放彩条、配音、字幕、标题，保存后核对草稿内容，再试一次「不覆盖已有草稿」和「不碰别的草稿」两道保护。

把结果讲给使用者：通过几项、不通过几项、记录几项、跳过几项。「记录」不是失败，最常见的是配音没联网，脚本已经改用备用字幕继续。「跳过」是没装听写时，听写那一项不测。

## 第 6 步：请使用者在剪映里看一眼

自检输出最后也列了几条「需要人来做的」，按下面的说法讲给使用者（以这里为准）：

1. 请使用者**完全关掉剪映，再重新打开**。剪映开着时新建的草稿不会出现在首页列表里，回到首页也看不到。可以用 `Get-Process JianyingPro -ErrorAction SilentlyContinue` 确认剪映已经关掉（没有输出就是关掉了）。
2. 打开自检草稿。完整名字在自检输出的「新建草稿」那一行，形如 `JA自检_1020_121610`。剪映首页会把名字截短成「JA自检_…21610」这样，所以**把完整名字和末 5 位都告诉使用者**，让他按末 5 位找。
3. 应该看到：竖版彩条、顶部黄色标题、底部白字黑边字幕、一段配音。点一条字幕，右侧有「字幕」页签。
4. 点右上角「导出」，确认能导出。
5. 看完可以在剪映里把自检草稿删掉。

## 第 7 步：收尾

告诉使用者：

1. **完全退出并重开 Claude**，新会话里才有剪映的工具。
2. 以后说「用剪映给这段视频加字幕和配音」之类的话就行，规则技能 `ja-jianying` 会自动带上。
3. 导出永远要他自己点；剪映打开过的草稿 AI 读不回来，要改就让 Claude 新建一版。
4. 剪映开着自动更新，升级后先让 Claude 跑一次自检。

最后用下面的格式汇报，数字照实填：

```
安装结果：成功 / 没装完（原因：…）
剪映版本：…（实测过 / 没实测过）    听写：装了 / 没装
自检：通过 … 项，不通过 … 项，记录 … 项，跳过 … 项
在剪映里看过自检草稿：是 / 否
需要你做的：…
没有验证的：…
```
