# 复现卡 · {{CASE_ID}} · {{TITLE}}

> 执行时只读本卡。需要证据或完整背景时再打开 `operation-profile.md` / `steps.json`。
> 软件：{{SOFTWARE}} ｜ 必做步骤 {{N}} 步 ｜ 建档 {{DATE}}

## 变量（开跑前逐项确认）

| 变量 | 本次取值 |
|---|---|
| `{{INPUT_VIDEO}}` | {{待填}} |
| `{{OUTPUT_DIR}}` | {{待填}} |
| {{VAR}} | {{待填}} |

## 前置检查

- [ ] 目标软件已打开，工程状态符合 `preconditions`
- [ ] 输入素材存在、输出目录已建且无同名文件
- [ ] 已获授权执行真实点击／写文件；危险操作白名单：{{无 / 逐项列出}}
- [ ] `questions.md` 中的待确认项已全部关闭

## 步骤

| # | 前置状态 | 动作 | 参数 | 完成后应看到 | 失败处理 |
|---|---|---|---|---|---|
| S01 | {{界面应为…}} | {{gui: 媒体库 → 导入}} | `{{INPUT_VIDEO}}` | {{素材缩略图出现}} | 暂停 → 重试／跳过／人工接管 |
| S02 | | | | | |

> 分支：{{条件 → 跳转，或「无」}}；循环：{{说明，或「无」}}
> 可选步骤（不计入完成率）：{{列出 Sxx，或「无」}}

## 执行约定

- 每步执行后截图到 `runs/{{RUN_ID}}/shots/<step_id>.png`，与档案关键帧比对。
- 每步立即写入 `replay-log.json`：`step_id, channel, status, observed, screenshot, note`。
- 任一步失败立即暂停，不自动继续、不猜测控件、不美化状态。
- 异步任务（识别／渲染／导出）设置等待上限，超时标 `failed`。

## 收尾

```
python "{SKILL_DIR}/scripts/run.py" verify --steps "{CASE_DIR}/steps.json" --log "{RUN_DIR}/replay-log.json"
```

把完成率、全部偏差与未复现步骤交用户确认后，再写 `replay-notes.md` 并交付。
