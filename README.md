# BUAA Course Skills

把 BUAA 课堂回放、课程转写和课次元信息，整理成**可检索、可回看、可复用**的课程学习资料。  
它既可以生成单次课程纪要，也可以继续沉淀到 Obsidian，形成长期维护的课程知识库。

## 这个仓库解决什么问题

课堂回放通常能播放，但不容易真正用于复习：

- 过几周后，很难快速回忆某一节课到底讲了什么
- 想回看某个知识点时，不知道应该从哪一段开始
- 老师提到的作业、考试、练习和提醒，分散在整节课里
- 一学期结束后，留下的往往是一堆零散转写，而不是结构化课程资料

这个仓库的目标，是把“能播放的课堂回放”变成“可以长期复用的学习资料”。

## 仓库包含两个 skill

### `buaa-classroom-summarizer`

从 BUAA `livingroom` 或 `coursedetail` 页面提取结构化回放原料，并生成单次课程纪要。

适合你想要：

- 枚举可回放课次
- 提取课程转写和元信息
- 导出独立 Markdown 纪要
- 快速知道这节课讲了什么、强调了什么、布置了什么

### `obsidian-course-vault`

把课程原料和课次纪要整理进 Obsidian，维护课程总览、课次页、概念页和图谱 hub。

适合你想要：

- 持续维护一门课的知识库
- 把单节课笔记逐渐长成课程体系
- 在 Obsidian 中建立概念页、图谱入口和课程总览

## 典型工作流

1. 从 BUAA 课堂页面提取回放、转写和课次元信息  
2. 生成单次课程纪要或语义重建 packet  
3. 将结果同步到 Obsidian  
4. 持续维护课次页、概念页、图谱 hub 和课程总览

两个 skill 可以单独使用，也可以串联使用。

## 你最终会得到什么

使用 `buaa-classroom-summarizer` 时，典型输出包括：

- 一份按课次组织的 Markdown 纪要
- 本节主线总结
- 带时间戳的内容时间轴
- 关键概念整理
- 课程事务 / 作业 / 待核对信息
- 面向复习的回看建议

如果再配合 `obsidian-course-vault`，还可以继续维护：

- 课程总览页
- 单次课次页
- 概念页
- 课程图谱 hub
- 回放同步页

## 正式支持的输入入口

当前正式支持：

- `classroom.msa.buaa.edu.cn/livingroom`
- `classroom.msa.buaa.edu.cn/coursedetail`

## 快速开始

第一次使用，建议先安装 `buaa-classroom-summarizer`。  
只有当你希望长期维护 Obsidian 课程库时，再加装 `obsidian-course-vault`。

安装 `buaa-classroom-summarizer`：

```bash
npx skills add https://github.com/1xiaoooo/buaa-course-skills --skill buaa-classroom-summarizer --yes --global
```

安装 `obsidian-course-vault`：

```bash
npx skills add https://github.com/1xiaoooo/buaa-course-skills --skill obsidian-course-vault --yes --global
```

如果只想安装到当前项目，可以去掉 `--global`：

```bash
npx skills add https://github.com/1xiaoooo/buaa-course-skills --skill buaa-classroom-summarizer --yes
npx skills add https://github.com/1xiaoooo/buaa-course-skills --skill obsidian-course-vault --yes
```

安装完成后，请分别补充依赖，并阅读各自的 `SKILL.md`。详细说明见 [INSTALL.md](INSTALL.md)。

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

## 适合的使用场景

- 想系统复盘一门课，而不是只看单节回放
- 想快速了解某节课课堂上发生了什么
- 想把课程资料逐渐沉淀为自己的知识体系
- 已经在使用 Obsidian，希望把课程资料结构化维护起来

## 当前不太适合的场景

- 希望完全零介入、一键得到完美课堂笔记
- 希望在任何课程、任何平台上直接无适配使用
- 希望在转写质量较差的情况下仍然稳定得到高质量结果

## 当前状态

当前仓库处于 `public beta` 阶段，已经比较稳的部分包括：

- BUAA 课堂回放抽取
- 转写优先的结构化诊断
- 独立 Markdown 纪要导出
- Obsidian 课程库、课次页、概念页与图谱入口维护

仍需持续验证和打磨的部分包括：

- 跨平台实机验证
- 非 Windows 环境下的认证与运行细节
- 受原始 ASR / 转写质量影响较大的课程整理效果

## 仓库结构

```text
skills/
  buaa-classroom-summarizer/
  obsidian-course-vault/
INSTALL.md
RELEASE_CHECKLIST.md
```

## 发布说明

- License: `MIT`
- 当前推荐以 `public beta` 的预期使用，而不是视为完全无监督的稳定产品
- 用户可以通过上面的 `npx skills add ...` 命令直接安装
