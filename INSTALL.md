# 安装说明

推荐安装方式是直接使用 skills CLI。

## 安装命令

推荐全局安装：

```bash
npx skills add https://github.com/1xiaoooo/buaa-course-skills --skill buaa-classroom-summarizer --yes --global
npx skills add https://github.com/1xiaoooo/buaa-course-skills --skill obsidian-course-vault --yes --global
```

如果只想安装到当前项目，可以去掉 `--global`：

```bash
npx skills add https://github.com/1xiaoooo/buaa-course-skills --skill buaa-classroom-summarizer --yes
npx skills add https://github.com/1xiaoooo/buaa-course-skills --skill obsidian-course-vault --yes
```

安装完成后，Codex / Claude Code 会把 skill 放到各自的用户技能目录中。

## 依赖安装

安装完成后，分别进入每个 skill 目录安装依赖：

```powershell
cd skills\buaa-classroom-summarizer
python -m pip install -r requirements.txt

cd ..\obsidian-course-vault
python -m pip install -r requirements.txt
```

你另外通常还需要：

- `ffmpeg`
- 一个受支持的 Chromium 浏览器
- Obsidian（仅 `obsidian-course-vault` 需要）

## 使用前先确认

- 正式支持的入口只有 `livingroom` 和 `coursedetail`
- `vault-dir` 需要显式传入
- 课程转写是唯一主来源，PPT 只是辅助

## 说明

- 当前 Windows 路径最顺。
- 在非 Windows 环境下，更推荐 runtime browser auth，而不是本地 cookie 解密。
- 第一次使用前，先读每个 skill 目录下的 `SKILL.md`。
- 如果后续公开安装量进入 skills 生态统计，这两个 skill 也有机会被 `find-skills` 搜到。
