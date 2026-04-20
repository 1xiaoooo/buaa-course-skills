# BUAA Course Skills

面向北航课程场景的 `public beta` 技能仓库，用来把课堂回放、课程转写和课次元信息，整理成真正能复用的学习资料，而不是一次性摘要。

如果你属于下面这类人，这个仓库大概率会有用：

- 想系统复盘一门课，而不是只看单节回放
- 上课跟得不稳定，但又不想完全掉队
- 想快速知道老师这节课讲了什么、强调了什么、布置了什么
- 希望把一门课长期整理成自己的知识库，而不是只留一堆零散转写
- 已经在用 Obsidian，想把课程笔记、概念页和图谱结构一起长出来

这套 skill 想做的不是“替你听课”，而是把课堂回放变成一个更容易继续学习、继续整理、继续回看的起点。

这个仓库当前包含两个 skill：

- `buaa-classroom-summarizer`
  从 BUAA `livingroom` 或 `coursedetail` 页面提取结构化回放原料。
- `obsidian-course-vault`
  把这些原料整理进 Obsidian，维护课次页、概念页、图谱 hub、课程总览和回放同步页。

它们可以单独使用，也可以串起来使用：

- 只想拿到课程转写、元信息、回放索引和独立 Markdown 纪要：用 `buaa-classroom-summarizer`
- 想把一门课长期沉淀成 Obsidian 知识库：再加 `obsidian-course-vault`

## 它具体能帮你省掉什么

正常情况下，我们面对课堂回放时最烦的几件事是：

- 先到处找哪一节已经可回放
- 点进回放后再手动看有没有转写
- 自己把零散内容整理成一份像样的课次纪要
- 过几周后再回看时，已经不记得这节课和前后课是什么关系

这套仓库的目标，就是把这几步尽量收成一条稳定工作流：

- 先把回放原料抽出来
- 再基于课程转写整理成正式课次笔记
- 最后把概念页、图谱 hub 和课程总览一起维护起来

所以它更适合“持续整理一门课”，而不只是偶尔导出一份 transcript。

## 适合谁

最适合的用户通常是：

- 想自学或系统复盘课程的人
- 想快速了解某节课课堂上发生了什么的人
- 想把一学期课程逐渐沉淀成自己的知识体系的人
- 希望把“能播放的课堂回放”变成“可检索、可回看、可复用的学习资料”的人

如果你只是想临时看一眼回放能不能播放，这个仓库就有点重了；但如果你想把回放真正变成长期可复用资料，它就比较合适。

## 不太适合谁

这套仓库目前不太适合下面这类需求：

- 只想临时看一眼今天讲了什么，不想维护任何长期资料的人
- 想完全零介入、一键得到完美课堂笔记的人
- 不愿意处理浏览器登录、依赖安装或基础配置的人

换句话说，它更像一个“把课堂回放变成长期学习资料”的工作流，而不是一个随手即得的玩具摘要器。

## 为什么值得装

如果你已经习惯把知识真正沉淀下来，这两个 skill 的价值主要在这里：

- 它不会只给你一份一次性的摘要，而是能把一门课慢慢整理成体系
- 它能把原始回放、课程转写、课次纪要、概念页和课程图谱串起来
- 它在转写缺失、截断或质量不稳时，会显式降级，而不是假装已经整理完成
- 它会以课程转写为主，避免被课件截图、平台界面或其他辅助画面带偏

所以它更像是一个“课程资料整理基础设施”，而不是单次摘要工具。

## 正式支持的入口

当前正式支持的输入有：

- `classroom.msa.buaa.edu.cn/livingroom`
- `classroom.msa.buaa.edu.cn/coursedetail`

## 当前适合什么场景

已经比较稳的部分：

- BUAA 课堂回放抽取
- 转写优先的结构化诊断
- 独立 Markdown 纪要导出
- Obsidian 课程库维护、课次页维护、概念页维护和图谱入口维护

仍然属于 beta 的部分：

- 跨平台实机验证还不完整
- 非 Windows 环境更推荐 runtime browser auth
- 最终纪要质量仍会受到课程转写质量和课程结构影响

## 仓库结构

```text
skills/
  buaa-classroom-summarizer/
  obsidian-course-vault/
INSTALL.md
RELEASE_CHECKLIST.md
```

## 快速开始

发布到 GitHub 后，推荐直接用 skills CLI 安装。

安装 `buaa-classroom-summarizer`：

```bash
npx skills add <owner>/<repo> --skill buaa-classroom-summarizer
```

安装 `obsidian-course-vault`：

```bash
npx skills add <owner>/<repo> --skill obsidian-course-vault
```

如果你的仓库地址已经确定，也可以直接写成完整 GitHub URL：

```bash
npx skills add https://github.com/<owner>/<repo> --skill buaa-classroom-summarizer
npx skills add https://github.com/<owner>/<repo> --skill obsidian-course-vault
```

安装后再分别补依赖，并阅读各自 `SKILL.md`。详细说明见 [INSTALL.md](INSTALL.md)。

## 推荐用法

### 方案 A：只做回放抽取

使用 `skills/buaa-classroom-summarizer`

适合你想要：

- 枚举可回放课次
- 导出课程转写和元信息
- 生成独立 Markdown 课次纪要或语义重建 packet

### 方案 B：做完整课程知识库

两个 skill 一起用。

适合你想要：

- 持续维护的 Obsidian 课程库
- 能点开的概念页
- 可读的图谱 hub 和课程总览
- 一学期持续同步和整理回放

## 发布说明

- 仓库使用 `MIT` 许可证。
- 当前推荐以 `public beta` 心态使用，而不是把它当成完全无监督的稳定产品。
- 发布到公开 GitHub 仓库后，用户可以通过 `npx skills add ...` 安装。
