# 操作图谱 schema 与编写规范

`steps.json` 是技能的核心产物：既给人核对，也给复现阶段当执行依据。**写档案前必读。** 字段缺失比字段填错更容易导致复现失败，因此每一步都必须有可验收的 `expected` 和可回溯的 `evidence`。

## 1. 顶层结构

```json
{
  "schema_version": 1,
  "case_id": "jianying-ai-subtitle",
  "title": "用 AI 自动字幕给口播视频加字幕",
  "source": {
    "video": "D:/教程/ai-subtitle.mp4",
    "url": null,
    "duration_sec": 482,
    "software": ["剪映专业版"],
    "software_version": "教程中未标明"
  },
  "variables": {
    "INPUT_VIDEO": {"description": "待处理素材绝对路径", "example": "D:/素材/raw.mp4", "required": true},
    "OUTPUT_DIR": {"description": "成片输出目录", "example": "D:/成片/", "required": true},
    "RESOLUTION": {"description": "导出分辨率", "example": "1080P", "required": false}
  },
  "preconditions": ["已安装并登录剪映专业版", "素材为含人声的 mp4"],
  "steps": [ /* 见下 */ ],
  "branches": [],
  "open_questions": [],
  "confidence_summary": {"high": 6, "medium": 2, "low": 1, "unverified": 1}
}
```

`variables` 中的键用大写下划线命名，在步骤里以 `{{INPUT_VIDEO}}` 引用。复现时只替换变量，不改步骤主体。

## 2. step 字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `step_id` | string | 是 | `S01`、`S02`… 稳定不变，复现日志靠它对应 |
| `time_range` | `[start, end]` 秒 | 是 | 该操作在**原视频**中的时间区间，来自 `evidence.json` |
| `intent` | string | 是 | 一句话说清这一步要达成什么（动词开头） |
| `action` | object | 是 | 动作描述，见第 3 节 |
| `params` | object | 否 | 参数键值；值可引用 `{{VAR}}` |
| `precondition_state` | string | 是 | 执行前界面应当是什么样（可观察） |
| `expected` | string | 是 | **可验收**的执行后状态，见第 5 节 |
| `evidence` | array | 是 | 至少一条，见第 4 节 |
| `confidence` | `high` / `medium` / `low` | 是 | 判定规则见第 6 节 |
| `unverified` | array[string] | 否 | 未确认的具体项，同步写进 `questions.md` |
| `required` | bool | 否 | 默认 `true`；可选步骤设 `false`，`verify` 会区别对待 |
| `depends_on` | array[string] | 否 | 前置步骤 id |
| `notes` | string | 否 | 易错点、软件版本差异、教程中的口头提醒 |

## 3. action.type 枚举

| type | 含义 | `target` 写法示例 |
|---|---|---|
| `gui` | 界面点击／菜单／拖拽 | `媒体库 → 导入按钮`、`时间线 → 片段右键菜单` |
| `input` | 键入文本或数值 | `转场时长输入框`、`字幕文本内容` |
| `shortcut` | 键盘快捷键（**仅当画面或台词明确给出**） | `Ctrl+K` |
| `file` | 文件／目录操作 | `把素材复制到 {{OUTPUT_DIR}}` |
| `command` | 终端命令、脚本、软件 CLI/API | `ffmpeg -i ...` |
| `wait` | 等待处理完成 | `等待 AI 字幕识别进度到 100%` |
| `verify` | 检查而非改变状态 | `预览窗口播放前 5 秒` |
| `decision` | 需要用户判断才能继续 | `按素材语种选择识别语言` |

`action` 至少含 `type` 与 `target`。**不要写屏幕坐标**——不同分辨率与 DPI 下坐标无效，且教程录屏常不显示光标。

## 4. evidence 条目

```json
{
  "frame": "evidence/frames/frame_0037.jpg",
  "time_sec": 111,
  "speech": "这里把识别语言改成中文，不然会出英文",
  "note": "左侧面板 1:49 处显示语言下拉为「中文」",
  "dense_frame": "evidence/dense/d_0008.jpg"
}
```

- 路径相对 `{CASE_DIR}`。
- `speech` 取自 `timeline.json` 对应帧；无转录时省略，不要编造。
- 补抽密帧加 `dense_frame` 并注明换算后的原视频时间。
- 一步可有多条证据（操作前 + 操作后），建议至少覆盖状态变化的两端。

## 5. expected 的写法

必须**可判定、可违反**，写执行后能直接观察到的状态，不写「设置好」「处理完成」这类空话。

| 不合格 | 合格 |
|---|---|
| 「设置转场」 | 「两个片段之间出现转场图标，右侧面板时长显示 0.5s」 |
| 「导出视频」 | 「`{{OUTPUT_DIR}}` 下出现 .mp4 文件，分辨率 1080×1920」 |
| 「调整参数」 | 「降噪滑块数值显示为 35，预览窗口噪点明显减少」 |
| 「加字幕」 | 「时间线上方新增字幕轨道，条数与口播句数一致」 |

`verify` 只做日志与档案比对；`expected` 是否真的达成，由 Agent 对照实际截图判断。

## 6. 置信度规则

- `high`：画面清晰可见控件与数值，台词与画面一致，前后状态差明确。
- `medium`：动作可见但参数读不全，或口述与画面略有错位，或仅靠单帧推断。
- `low`：参数被遮挡、帧间隔漏掉关键动作、疑似剪辑跳过、软件版本不明导致界面差异。
- 任何 `low` 或带 `unverified` 的步骤，**必须**在确认节点 1 单独列出请用户确认；未确认前不得进入复现。

## 7. 分支、循环与可选步骤

- 教程里的「如果想…就…」写进 `branches`：`{"condition": "素材为竖屏", "goto": "S07", "else": "S09"}`，不要塞进线性步骤。
- 重复多次的同类操作（给 5 个片段逐个加转场）写成一步 + `notes` 说明「对每个片段重复」，或用 `loop` 字段：`{"over": "{{CLIP_LIST}}", "as": "clip"}`。
- 与核心目标无关的可选美化步骤设 `required: false`，`verify` 不会把它的缺席算作失败。

## 8. 从观察到步骤的归并原则

1. 只保留**界面状态发生变化**的区间；讲解、结果回放、片头片尾不生成步骤。
2. 连续多帧属于同一动作时合并为一步，`time_range` 覆盖整个区间，证据取首尾帧。
3. 一个动作产生多个参数变化但属于同一面板，合并为一步，参数写进 `params`。
4. 无法归并到具体动作的画面变化，宁可写成一条 `verify` 步骤或列入 `open_questions`，也不要编造点击。

## 9. 写入前自检

- [ ] 每一步都有 `expected`，且能被截图或文件状态验证
- [ ] 每一步都有 `evidence`，路径真实存在
- [ ] 具体路径、素材名、时长、输出目录都已抽成 `variables`
- [ ] `time_range` 与 `evidence.json` 的帧时间一致（含 `start_time_offset_sec`）
- [ ] 所有 `low` / `unverified` 项同步进了 `questions.md`
- [ ] 没有凭常识补写的默认值、快捷键、坐标
- [ ] `step_id` 连续且与 `replay-log.json` 可一一对应
