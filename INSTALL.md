# 安装说明

推荐把这两个 skill 当成“先安装，再补依赖，再开始用”的工具，而不是只复制仓库文件。

## 1. 安装 skill

推荐全局安装：

```bash
npx skills add https://github.com/1xiaoooo/buaa-course-skills --skill buaa-classroom-summarizer --yes --global
npx skills add https://github.com/1xiaoooo/buaa-course-skills --skill obsidian-course-vault --yes --global
```

如果你只想安装到当前项目，可以去掉 `--global`：

```bash
npx skills add https://github.com/1xiaoooo/buaa-course-skills --skill buaa-classroom-summarizer --yes
npx skills add https://github.com/1xiaoooo/buaa-course-skills --skill obsidian-course-vault --yes
```

安装完成后，重启 Codex / Claude Code，让新 skill 生效。

## 2. 找到安装后的 skill 目录

`npx skills add ...` 安装的是“skill 副本”，不是直接在这个仓库目录里运行。

常见位置：

- Codex：`%USERPROFILE%\\.codex\\skills\\`
- Claude Code：通常也会放在自己的用户 skill 目录；如果你不确定，请直接在本机搜索 `buaa-classroom-summarizer` 或 `obsidian-course-vault`

如果你是全局安装到 Codex，通常直接进入下面两个目录即可：

```powershell
cd $env:USERPROFILE\.codex\skills\buaa-classroom-summarizer
cd $env:USERPROFILE\.codex\skills\obsidian-course-vault
```

## 3. 安装 Python 依赖

安装完成后，请在“安装后的 skill 目录”里补依赖，而不是在这个 GitHub 仓库根目录里执行。

`buaa-classroom-summarizer`：

```powershell
cd $env:USERPROFILE\.codex\skills\buaa-classroom-summarizer
python -m pip install -r requirements.txt
python -m playwright install chromium
```

`obsidian-course-vault`：

```powershell
cd $env:USERPROFILE\.codex\skills\obsidian-course-vault
python -m pip install -r requirements.txt
```

如果你不是装在 Codex 默认目录，请把上面的路径替换成你自己的实际安装位置。

## 4. 额外环境准备

通常还需要：

- Python 3.10 或更高版本
- `ffmpeg`
- 一个受支持的 Chromium 浏览器
  - Windows 下优先 Edge / Chrome
- Obsidian
  - 只有 `obsidian-course-vault` 需要

## 5. 首次使用前先确认

- 正式支持的入口只有：
  - `classroom.msa.buaa.edu.cn/livingroom`
  - `classroom.msa.buaa.edu.cn/coursedetail`
- `vault-dir` 必须显式传入，不会自动猜
- 课程转写是唯一主来源，PPT 只是辅助

## 6. 推荐的第一次使用顺序

如果你是第一次用，建议按这个顺序来：

1. 先只安装 `buaa-classroom-summarizer`
2. 用一节课或一门课的 `coursedetail` 先跑通回放抽取
3. 确认登录态、转写、输出目录都正常
4. 再决定是否接 `obsidian-course-vault`

## 7. 平台建议

- 当前最顺的是 Windows
- 在非 Windows 环境下，更推荐 `runtime browser auth`
- 不要优先指望本地浏览器 cookie 解密跨平台稳定可用

## 8. 读哪个文档

安装完以后：

- 想只做回放抽取：先看 `buaa-classroom-summarizer/SKILL.md`
- 想接 Obsidian：再看 `obsidian-course-vault/SKILL.md`

这个 `INSTALL.md` 只负责“怎么装、装完去哪里、首次怎么起步”，具体工作流细节都在各自 skill 的 `SKILL.md` 里。
