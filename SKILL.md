---
name: video-tutorial-learner
description: 从用户提供的视频教程（本地视频文件，或 B站 / 抖音 / YouTube 等在线链接）中学习并复现软件操作流程：抽关键帧、转录讲解、还原带时间戳的操作步骤与参数、沉淀为可长期复用的操作档案，之后按档案在新素材或新工程上把操作重做一遍并逐步校验。当用户给出一段教程视频要求「照着做一遍」「学会里面的操作」「把这个 AI 剪辑流程复刻到我的素材上」，或说「按上次那个教程再来一次」时使用。Triggers: 视频教程学习、教程操作复现、跟着视频做、软件操作提取、剪辑流程复刻、录屏教程解析、learn from tutorial video, replay workflow from screen recording.
agent_created: true
---

# 视频教程操作学习与复现

## 目的与边界

把一段视频教程变成三样东西：**可追溯的取证集**（关键帧、逐字稿、时间轴）、**结构化的操作图谱**（步骤／参数／验收点／证据）、**能在新任务上复现的执行记录与校验报告**。典型场景是照着 AI 软件或剪辑软件的教程，把同一套操作应用到自己的素材上。

必须遵守：

- 脚本只做**确定性取证与文本对齐**。界面识别、动作归纳、参数读取、复现是否成功，一律由 Agent 逐帧看图与比对截图判断；不得把 JSON 数字或 `similarity_hint` 当作结论。
- 看不清的参数、被遮挡的菜单、未展示的快捷键、鼠标悬停才出现的提示，一律标为 `unverified` 并列入 `questions.md`，**绝不臆造**。宁可少写一步，不可编造一步。
- 区分「真实操作」与「讲解铺垫／结果展示／重复回放」；只有界面状态真正发生变化的区间才生成步骤。
- 原始视频只在工作区本地处理；联网下载（`fetch`）与语音转录（会送出音轨）都必须先向用户说明并取得确认。涉及账号、密钥、隐私界面时，对关键帧打码或跳过该段。
- 复现前必须确认目标对象、写入权限与危险操作边界。**绝不自动执行删除、覆盖原文件、发布、支付、发送消息**。
- 只保留必要关键帧用于本地比对，不复制教程素材、不转存教程视频用于分发。

## 输入与前置确认

- 本地视频：给绝对路径，支持 mp4 / mov / mkv / webm / flv / avi。
- 在线链接：B站、YouTube 及通用网页视频走 `fetch`（需 yt-dlp）；抖音链接优先调用用户已安装的 `douyin-video-downloader` 技能下载到本地后再处理。
- 只给视频、没说诉求：先问清是「只理解流程」「学完存档」还是「立刻复现」，默认按**学完存档**处理，不擅自开始操作本机软件。
- 教程超过 30 分钟、画面低于 720p、或含大段无关片头：建议先裁剪到目标片段，或增大 `--interval`；在交付说明里写明置信度下降。
- 纯画面无音轨：走画面 + 内嵌字幕路线，跳过转录对齐，并在档案中标注「无口述佐证，参数置信度降低」。

## 产物位置约定

`{SKILL_DIR}` 指本 SKILL.md 所在目录的绝对路径；`{CASE_DIR}` 指某个教程案例目录。执行时展开为真实绝对路径，所有路径参数用英文双引号包裹。

档案库放在**当前工作区**（本用户环境下为 `D:/yunpan/AI`），不写进技能安装目录：

```
{WORKSPACE}/video-tutorial-learner/cases/<slug>/
├── source/                  # 下载的原视频（可选，也可只记路径）
├── evidence/                # frames/ scenes/ contact/ audio.wav subtitles.srt
│   ├── evidence.json        # 帧清单、音视频元数据、警告
│   ├── transcript.*         # 转录原始结果
│   └── timeline.json        # 帧 ↔ 台词时间轴
├── steps.json               # 操作图谱（机器读）
├── operation-profile.md     # 完整档案（人读）
├── replay-card.md           # 精简复现卡（≤ 1 页，执行时只读它）
├── questions.md             # 待用户确认项
└── runs/<日期>-<目标>/
    ├── replay-log.json
    ├── verification.json
    ├── shots/               # 每步实际截图
    └── replay-notes.md
```

`<slug>` 由教程主题生成，短、小写、用连字符，例如 `jianying-ai-subtitle`。同一教程的第二个视频用 `<slug>-2`，不静默合并。

## 工作流

### 步骤 0 · 环境自检

```
python "{SKILL_DIR}/scripts/run.py" check
```

读取 `warnings`：ffmpeg/ffprobe 不可用则停止并给安装指引；缺 yt-dlp 只影响在线链接，需先向用户确认再联网安装（`python -m pip install yt-dlp`），本地视频不受影响。

### 步骤 1 · 确认诉求与输入（确认节点 0）

区分三种诉求：**只理解流程** / **学完存档**（默认） / **立刻复现**。

向用户列出并确认：输入来源、诉求、产物目录、是否允许联网下载、是否允许把音轨送去做语音转录。确认后再动手。抖音链接直接改走 `douyin-video-downloader` 技能。

### 步骤 2 · 取得视频并探测

```
python "{SKILL_DIR}/scripts/run.py" probe --input "D:/.../tutorial.mp4"
python "{SKILL_DIR}/scripts/run.py" fetch --url "https://..." --out-dir "{CASE_DIR}/source"
```

记住 `duration_sec`、`width/height`、`has_audio`、`has_embedded_subtitles`。分辨率过低或无音轨时提前告知用户。

### 步骤 3 · 取证

```
python "{SKILL_DIR}/scripts/run.py" extract --input "D:/.../tutorial.mp4" --out-dir "{CASE_DIR}/evidence" --interval 3
```

调参见 `references/evidence-guide.md`：菜单弹出／快速点击用 1–2 秒，常规软件演示用 3 秒，长教程用 5–8 秒。`--max-frames` 默认 200，超限时脚本自动放宽间隔并在 `warnings` 说明。

必须逐条读 `evidence.json` 的 `warnings`，并把降级情况写进档案的置信度说明。

### 步骤 4 · 转录与对齐

- 有 `audio.wav`：用工具检索找到语音转录能力，调用时传入 `filePath` 指向该 wav；**按实际返回格式**把结果原样保存为 `evidence/transcript.json` / `.srt` / `.txt`，不得伪造时间戳。返回纯文本时存 `.txt`。
- 有 `subtitles.srt`：与转录结果交叉校对参数数值；两者冲突时以画面为准。
- 不允转录或无音轨：跳过本步，档案注明「纯画面路线」。

```
python "{SKILL_DIR}/scripts/run.py" align --evidence "{CASE_DIR}/evidence/evidence.json" --transcript "{CASE_DIR}/evidence/transcript.srt"
```

`timeline.json` 给每帧附上该时间段的台词，用于把「口述意图」对应到「画面动作」。详见 `references/evidence-guide.md`。

### 步骤 5 · 逐帧还原操作（核心，由 Agent 完成）

1. 先用图像查看能力通读 `evidence/contact/*.png` 建立全局节奏，再对存疑区间打开 `evidence/frames/*.jpg` 单帧细看；帧间隔过大时按 `references/evidence-guide.md` 的命令补抽密帧。
2. 按时间顺序写观察记录：软件与页面名称、可见控件、参数数值、前后状态差异、对应台词。
3. 归并成步骤，写入 `steps.json`（字段规范见 `references/operation-schema.md`）。每一步必须含 `intent`、`action`、`expected`（可验收）、`evidence`（帧路径 + 时间 + 台词）、`confidence`。
4. 把素材路径、时长、分辨率、输出目录、命名规则等抽成顶层 `variables`，让档案能迁移到新任务。
5. 所有不确定项写入 `questions.md`，标注需要用户提供的具体信息（例如「第 4 步的降噪阈值在画面中被面板遮挡」）。

### 步骤 6 · 步骤表核对（确认节点 1）

把 `steps.json` 渲染成简表给用户：`编号 | 时间 | 动作 | 参数 | 验收点 | 置信度`。明确询问：哪些步骤要增删改？哪些参数要替换成用户环境的值？低置信度项如何确认？

确认后再以 `assets/operation-profile-template.md` 和 `assets/replay-card-template.md` 为骨架写档案与复现卡。`replay-card.md` 控制在 1 页内，执行阶段只读它，需要证据时再回查完整档案。

### 步骤 7 · 复现（确认节点 2）

开跑前确认：目标软件／工程／素材的绝对路径、是否允许真实点击与写文件、输出目录、危险操作白名单、失败时是否自动继续。**未确认不得执行任何写操作。**

按 `references/replay-guide.md` 选通道：

- **通道 A · GUI 复现**：调用桌面控制或浏览器控制能力，按 `replay-card.md` 逐步操作；每步后截图存 `runs/.../shots/<step_id>.png`，与档案关键帧对照后再进入下一步。
- **通道 B · 可脚本化**：把 ffmpeg、文件批处理、软件命令行／API 类步骤写成脚本，**先 dry-run 打印将执行的命令**，用户确认后再真正运行。
- **通道 C · 指令清单**：无桌面控制能力时，输出逐条可照做的指令与每步期望界面，由用户手动执行并回报结果。

每步立即写入 `replay-log.json`：`step_id, status(ok|deviation|failed|skipped), observed, screenshot, note`。任一步失败即暂停，给出「重试 / 跳过 / 人工接管」三个选项，不自行猜测下一步。

### 步骤 8 · 校验与交付（确认节点 3）

```
python "{SKILL_DIR}/scripts/run.py" verify --steps "{CASE_DIR}/steps.json" --log "{RUN_DIR}/replay-log.json"
```

- 逐条处理 `pending_actions`；`similarity_hint` 只是中文二元组重合度提示，结论以截图比对与 `status` 为准。
- 交付前把「必做步骤完成率、偏差项、未复现步骤」摆给用户确认，不隐瞒失败项。
- 用 `present_files` 呈现 `operation-profile.md`、`steps.json`、`verification.json`、关键接触表与（若产出）成品文件，按用户当下诉求排序，复现任务把成品／复现记录放第一位。
- 对话里给：一句话流程结论、完成率、主要偏差与原因、文件路径、复用方式。

## 档案复用与迭代

- 用户说「按上次那个教程再做一遍」：列 `cases/` 目录定位；存在多个候选要问清是哪一个，不猜。
- 换素材复现：只改 `variables` 与 `runs/` 目标，步骤主体不动；软件版本或界面变化时重新抽帧核对对应步骤。
- 用户纠正某步：更新 `steps.json` 并在 `operation-profile.md` 的修订记录写日期与原因。
- 长期未用的档案再次调用前，先比对当前软件界面与档案关键帧，差异大时提示用户确认。

## 资源

- `scripts/run.py` — 唯一入口，分发 `check` / `probe` / `fetch` / `extract` / `align` / `verify`；零第三方 Python 依赖。
- `references/evidence-guide.md` — 抽帧参数、降级处理、补抽密帧命令、时间戳对齐规则、常见误判。**取证与对齐前必读**。
- `references/operation-schema.md` — `steps.json` 字段定义、动作类型枚举、置信度规则、验收点与变量化写法。**写档案前必读**。
- `references/replay-guide.md` — 三种复现通道、GUI 操作要点、剪辑类软件动作映射、失败处理与安全边界。**复现前必读**。
- `assets/operation-profile-template.md` — 完整操作档案骨架。
- `assets/replay-card-template.md` — 精简复现卡骨架。
- `assets/steps-example.json` — 可直接改写的 `steps.json` 示例。

## 典型用户说法

「照着这个视频把操作做一遍」「学一下这个教程里的剪辑流程」「把这个 AI 软件的操作步骤整理出来」「按上次那个教程处理我新录的素材」「这段教程里第 3 步参数看不清，帮我确认一下」「照着做完了，帮我核对哪几步没对上」。
