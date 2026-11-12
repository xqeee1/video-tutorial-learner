#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
video-tutorial-learner :: 视频教程取证与复现工具链（唯一入口）

设计约束：
- 零第三方 Python 依赖；所有媒体处理交给 ffmpeg / ffprobe。
- 只做「确定性取证与校验」，不做语义理解。界面识别、动作归纳、参数判断由 Agent 完成。
- 所有产物落在用户显式给出的 --out-dir 内，不写系统目录、不联网（fetch 子命令除外）。

子命令：
  check    环境自检
  probe    读取视频元数据
  fetch    下载在线视频（需 yt-dlp，缺失时给出安装指引）
  extract  抽关键帧 + 时间戳水印 + 接触表 + 音轨 + 内嵌字幕 + evidence.json
  align    把 ASR / 字幕结果对齐到帧时间轴，输出 timeline.json
  verify   把复现日志与操作步骤档案比对，输出 verification.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

SCHEMA_VERSION = 1
FFMPEG = os.environ.get("VTL_FFMPEG") or shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = os.environ.get("VTL_FFPROBE") or shutil.which("ffprobe") or "ffprobe"

FONT_CANDIDATES = [
    os.environ.get("VTL_FONT"),
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/msyh.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/Library/Fonts/Arial.ttf",
]


# --------------------------------------------------------------------------- #
# 基础设施
# --------------------------------------------------------------------------- #

def emit(obj, code=0):
    print(json.dumps(obj, ensure_ascii=False, indent=2))
    return code


def fail(message, **extra):
    payload = {"ok": False, "error": message}
    payload.update(extra)
    return emit(payload, 2)


def run_cmd(cmd, *, timeout=1800, cwd=None):
    try:
        p = subprocess.run(
            [str(c) for c in cmd],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            cwd=str(cwd) if cwd else None,
        )
        return p.returncode, p.stdout or "", p.stderr or ""
    except FileNotFoundError:
        return 127, "", "command not found: %s" % cmd[0]
    except subprocess.TimeoutExpired:
        return 124, "", "timeout after %ss" % timeout


def find_font():
    for candidate in FONT_CANDIDATES:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    return None


def find_ytdlp():
    exe = shutil.which("yt-dlp")
    if exe:
        return [exe]
    rc, _, _ = run_cmd([sys.executable, "-m", "yt_dlp", "--version"], timeout=30)
    if rc == 0:
        return [sys.executable, "-m", "yt_dlp"]
    return None


def parse_rate(value):
    """把 '30000/1001' / '25' 转成 float。"""
    if not value:
        return None
    try:
        if "/" in value:
            a, b = value.split("/", 1)
            return round(float(a) / float(b), 4) if float(b) else None
        return round(float(value), 4)
    except (ValueError, ZeroDivisionError):
        return None


def as_float(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def file_sha1(path, limit_mb=64):
    """只对前 64MB 取样，避免大视频哈希耗时过长。"""
    h = hashlib.sha1()
    try:
        with open(path, "rb") as fh:
            h.update(fh.read(limit_mb * 1024 * 1024))
        return h.hexdigest()
    except OSError:
        return None


def fmt_clock(seconds):
    seconds = max(0.0, float(seconds))
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return "%02d:%02d:%02d" % (h, m, s)


def probe_video(path):
    rc, so, se = run_cmd(
        [FFPROBE, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
        timeout=180,
    )
    if rc != 0:
        return {"ok": False, "error": "ffprobe failed", "stderr": se.strip()[-800:]}
    try:
        raw = json.loads(so)
    except json.JSONDecodeError:
        return {"ok": False, "error": "ffprobe returned non-JSON output"}

    streams = raw.get("streams", [])
    fmt = raw.get("format", {})
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audios = [s for s in streams if s.get("codec_type") == "audio"]
    subs = [s for s in streams if s.get("codec_type") == "subtitle"]

    duration = as_float(fmt.get("duration")) or as_float((video or {}).get("duration"))
    if duration is None:
        duration = 0.0

    return {
        "ok": True,
        "path": str(Path(path).resolve()),
        "size_bytes": int(as_float(fmt.get("size"), 0)),
        "sha1_sample": file_sha1(path),
        "duration_sec": round(duration, 3),
        "start_time_sec": round(as_float(fmt.get("start_time"), 0.0), 3),
        "width": (video or {}).get("width"),
        "height": (video or {}).get("height"),
        "fps": parse_rate((video or {}).get("r_frame_rate")),
        "video_codec": (video or {}).get("codec_name"),
        "has_audio": bool(audios),
        "audio_streams": len(audios),
        "has_embedded_subtitles": bool(subs),
        "format_name": fmt.get("format_name"),
    }


# --------------------------------------------------------------------------- #
# check
# --------------------------------------------------------------------------- #

def cmd_check(_args):
    tools = {}
    probes = [
        ("ffmpeg", [FFMPEG, "-version"]),
        ("ffprobe", [FFPROBE, "-version"]),
    ]
    for name, cmd in probes:
        rc, so, se = run_cmd(cmd, timeout=40)
        first = (so or se).strip().splitlines()[0] if (so or se).strip() else ""
        tools[name] = {"path": FFMPEG if name == "ffmpeg" else FFPROBE,
                       "ok": rc == 0, "version": first[:120]}

    ytdlp = find_ytdlp()
    tools["yt_dlp"] = {
        "path": " ".join(ytdlp) if ytdlp else None,
        "ok": bool(ytdlp),
        "version": None,
        "required_only_for": "在线视频下载（fetch 子命令）",
    }
    font = find_font()
    warnings = []
    if not tools["ffmpeg"]["ok"] or not tools["ffprobe"]["ok"]:
        warnings.append("ffmpeg/ffprobe 不可用，extract 无法执行；请安装并把路径写入 VTL_FFMPEG / VTL_FFPROBE 环境变量")
    if not ytdlp:
        warnings.append("未找到 yt-dlp；本地视频不受影响，在线链接需先确认后再安装：python -m pip install yt-dlp")
    if not font:
        warnings.append("未找到可用于时间戳水印的字体；帧图仍会生成，但不在画面上显示时间，请依赖 evidence.json 的 time_sec")

    return emit({
        "ok": tools["ffmpeg"]["ok"] and tools["ffprobe"]["ok"],
        "schema_version": SCHEMA_VERSION,
        "python": sys.version.split()[0],
        "tools": tools,
        "font": str(font) if font else None,
        "warnings": warnings,
    })


# --------------------------------------------------------------------------- #
# probe
# --------------------------------------------------------------------------- #

def cmd_probe(args):
    src = Path(args.input)
    if not src.is_file():
        return fail("输入视频不存在", input=str(src))
    result = probe_video(src)
    if not result.get("ok"):
        return fail(result.get("error", "probe failed"), **result)
    return emit(result)


# --------------------------------------------------------------------------- #
# fetch
# --------------------------------------------------------------------------- #

def cmd_fetch(args):
    url = args.url.strip()
    out = Path(args.out_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)

    if re.search(r"douyin|iesdouyin|v\.douyin", url, re.I):
        return emit({
            "ok": False,
            "reason": "douyin_link",
            "hint": "抖音链接优先调用用户已安装的 douyin-video-downloader 技能下载到本地，再把本地路径交给 extract。",
            "url": url,
            "out_dir": str(out),
        })

    ytdlp = find_ytdlp()
    if not ytdlp:
        return fail(
            "缺少 yt-dlp，无法下载在线视频",
            install_hint="先向用户确认联网安装，再执行：python -m pip install yt-dlp",
            url=url,
            fallback="也可让用户手动下载视频到本地，再用 extract 处理",
        )

    template = str(out / "source.%(ext)s")
    cmd = ytdlp + [
        "--no-playlist",
        "-f", "bv*[height<=1080]+ba/b[height<=1080]/b",
        "--merge-output-format", "mp4",
        "-o", template,
        url,
    ]
    rc, so, se = run_cmd(cmd, timeout=args.timeout)
    if rc != 0:
        return fail("下载失败", url=url, stderr=se.strip()[-1200:], stdout=so.strip()[-600:])

    candidates = sorted(
        [p for p in out.glob("source.*") if p.suffix.lower() in (".mp4", ".mkv", ".webm", ".mov", ".flv")],
        key=lambda p: p.stat().st_size,
        reverse=True,
    )
    if not candidates:
        return fail("下载命令成功但未找到输出文件", url=url, out_dir=str(out), stdout=so.strip()[-600:])

    video = candidates[0]
    meta = probe_video(video)
    return emit({
        "ok": True,
        "url": url,
        "downloaded": str(video.resolve()),
        "size_bytes": video.stat().st_size,
        "probe": meta,
        "next": 'python "{SKILL_DIR}/scripts/run.py" extract --input "<downloaded>" --out-dir "<case-dir>/evidence"',
    })


# --------------------------------------------------------------------------- #
# extract
# --------------------------------------------------------------------------- #

def _parse_scene_meta(text):
    """解析 metadata=print 输出，返回 [(time_sec, score)]。"""
    entries = []
    current_time = None
    for line in text.splitlines():
        m = re.search(r"pts_time:([0-9]+(?:\.[0-9]+)?)", line)
        if m and "frame:" in line:
            current_time = float(m.group(1))
            continue
        s = re.search(r"lavfi\.scene_score=([0-9]+(?:\.[0-9]+)?)", line)
        if s and current_time is not None:
            entries.append((round(current_time, 3), round(float(s.group(1)), 4)))
            current_time = None
    return entries


def _frames_from_dir(directory, pattern, kind, interval=None, scene_meta=None):
    files = sorted(directory.glob(pattern))
    result = []
    for idx, path in enumerate(files):
        item = {
            "index": idx,
            "file": path.as_posix(),
            "kind": kind,
            "time_sec": None,
        }
        if kind == "interval" and interval is not None:
            item["time_sec"] = round(idx * interval, 3)
            item["clock"] = fmt_clock(item["time_sec"])
        elif kind == "scene" and scene_meta and idx < len(scene_meta):
            item["time_sec"] = scene_meta[idx][0]
            item["scene_score"] = scene_meta[idx][1]
            item["clock"] = fmt_clock(item["time_sec"])
        result.append(item)
    return result


def cmd_extract(args):
    src = Path(args.input)
    if not src.is_file():
        return fail("输入视频不存在", input=str(src))

    out = Path(args.out_dir).resolve()
    frames_dir = out / "frames"
    scenes_dir = out / "scenes"
    contact_dir = out / "contact"
    work_dir = out / ".work"
    for d in (out, frames_dir, scenes_dir, contact_dir, work_dir):
        d.mkdir(parents=True, exist_ok=True)
    # 重复运行同一 out-dir 时清掉旧产物，避免旧帧混入时间轴
    for old in list(frames_dir.glob("frame_*.jpg")) + list(scenes_dir.glob("scene_*.jpg")) \
            + list(contact_dir.glob("sheet_*.png")):
        try:
            old.unlink()
        except OSError:
            pass

    meta = probe_video(src)
    if not meta.get("ok"):
        return fail(meta.get("error", "probe failed"), **meta)

    duration = meta["duration_sec"] or 0.0
    interval = float(args.interval)
    warnings = []

    # 帧数上限：自动放宽间隔，避免超长视频产出上万张图
    estimated = int(math.ceil(duration / interval)) + 1 if duration else args.max_frames
    if estimated > args.max_frames:
        new_interval = math.ceil((duration / args.max_frames) * 10) / 10 or interval
        warnings.append(
            "按 --interval %ss 估算约 %s 帧，超过上限 %s；自动调整为 %ss"
            % (interval, estimated, args.max_frames, new_interval)
        )
        interval = max(interval, new_interval)

    font = find_font()
    font_ready = False
    if font:
        try:
            shutil.copy2(font, work_dir / "font.ttf")
            font_ready = True
        except OSError as exc:
            warnings.append("字体复制失败，帧图不显示时间戳水印：%s" % exc)
    if not font_ready:
        warnings.append("未找到字体；帧图不显示时间戳水印，请依赖 evidence.json 的 time_sec 字段")

    width = int(args.width)
    base_vf = "fps=1/%s,scale=%d:-2" % (interval, width)
    watermark = (
        ",drawtext=fontfile=font.ttf:text='%{pts\\:hms}':x=16:y=14"
        ":fontsize=36:fontcolor=yellow:box=1:boxcolor=black@0.75:boxborderw=6"
    )

    # --- 1. 固定间隔帧 ---
    frame_pattern = str(frames_dir / "frame_%04d.jpg")
    head = [FFMPEG, "-hide_banner", "-loglevel", "error", "-i", str(src.resolve())]
    tail = ["-q:v", "3", "-strict", "unofficial", "-start_number", "0", "-y", frame_pattern]
    rc, _, se = run_cmd(head + ["-vf", base_vf + (watermark if font_ready else "")] + tail,
                        cwd=work_dir)
    drawn_timestamps = False
    if rc != 0 and font_ready:
        warnings.append("带水印抽帧失败，降级为无水印帧图：%s" % se.strip()[-300:])
        rc, _, se = run_cmd(head + ["-vf", base_vf] + tail, cwd=work_dir)
    elif rc == 0 and font_ready:
        drawn_timestamps = True
    if rc != 0:
        return fail("固定间隔抽帧失败", stderr=se.strip()[-1200:])

    frames = _frames_from_dir(frames_dir, "frame_*.jpg", "interval", interval=interval)
    if not frames:
        return fail("未生成任何关键帧，请检查视频是否可解码")

    # --- 2. 场景切换补充帧 ---
    scene_frames = []
    if not args.no_scenes:
        meta_file = work_dir / "scenes_meta.txt"
        meta_file.write_text("", encoding="utf-8")
        scene_vf = (
            "select='gt(scene,%s)',scale=%d:-2,metadata=print:file=scenes_meta.txt"
            % (args.scene, width)
        )
        rc2, _, se2 = run_cmd(
            [
                FFMPEG, "-hide_banner", "-loglevel", "error",
                "-i", str(src.resolve()),
                "-vf", scene_vf,
                "-fps_mode", "vfr",
                "-q:v", "3", "-strict", "unofficial", "-start_number", "0", "-y",
                str(scenes_dir / "scene_%04d.jpg"),
            ],
            cwd=work_dir,
        )
        if rc2 != 0:
            warnings.append("场景检测帧未生成（可能视频无镜头切换或参数不支持）：%s" % se2.strip()[-300:])
        else:
            scene_meta = _parse_scene_meta(meta_file.read_text(encoding="utf-8", errors="replace"))
            scene_frames = _frames_from_dir(scenes_dir, "scene_*.jpg", "scene", scene_meta=scene_meta)
            if scene_meta and len(scene_meta) != len(scene_frames):
                warnings.append(
                    "场景时间戳数量(%s)与场景帧数量(%s)不一致，scene 帧时间以文件顺序为准"
                    % (len(scene_meta), len(scene_frames))
                )
            if not scene_frames:
                warnings.append("未检测到显著镜头切换；对软件操作类教程属正常，固定间隔帧已足够")

    # --- 3. 接触表：按帧数选网格，避免短视频留下大片空白 ---
    n_frames = len(frames)
    if n_frames <= 6:
        cols, rows, tile_width = 3, 2, 480
    elif n_frames <= 12:
        cols, rows, tile_width = 4, 3, 480
    else:
        cols, rows, tile_width = 4, 5, 320
    per_sheet = cols * rows
    sheet_count = max(1, math.ceil(n_frames / per_sheet))
    tile_vf = "scale=%d:-2,format=yuv420p,tile=%dx%d:padding=4:margin=4:color=white" % (
        tile_width, cols, rows)
    rc3, _, se3 = run_cmd(
        [
            FFMPEG, "-hide_banner", "-loglevel", "error",
            "-start_number", "0",
            "-i", str(frames_dir / "frame_%04d.jpg"),
            "-vf", tile_vf,
            "-frames:v", str(sheet_count),
            "-y", str(contact_dir / "sheet_%02d.png"),
        ],
        cwd=work_dir,
    )
    sheets = []
    if rc3 != 0:
        warnings.append("接触表生成失败：%s" % se3.strip()[-300:])
    else:
        for i, path in enumerate(sorted(contact_dir.glob("sheet_*.png"))):
            lo = i * per_sheet
            hi = min(lo + per_sheet, len(frames)) - 1
            sheets.append({
                "file": path.as_posix(),
                "frame_range": [lo, hi],
                "time_range_sec": [frames[lo]["time_sec"], frames[hi]["time_sec"]],
            })

    # --- 4. 音轨 ---
    audio_rel = None
    if not args.no_audio and meta["has_audio"]:
        audio_path = out / "audio.wav"
        rc4, _, se4 = run_cmd(
            [FFMPEG, "-hide_banner", "-loglevel", "error", "-i", str(src.resolve()),
             "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", "-y", str(audio_path)],
            cwd=work_dir,
        )
        if rc4 == 0 and audio_path.is_file() and audio_path.stat().st_size > 1024:
            audio_rel = audio_path.as_posix()
        else:
            warnings.append("音轨提取失败：%s" % se4.strip()[-300:])
    elif not meta["has_audio"]:
        warnings.append("视频无音轨；教程可能依赖纯画面或内嵌字幕，需重点看帧图")

    # --- 5. 内嵌字幕 ---
    subtitle_rel = None
    if meta["has_embedded_subtitles"]:
        srt_path = out / "subtitles.srt"
        rc5, _, se5 = run_cmd(
            [FFMPEG, "-hide_banner", "-loglevel", "error", "-i", str(src.resolve()),
             "-map", "0:s:0", "-c:s", "srt", "-y", str(srt_path)],
            cwd=work_dir,
        )
        if rc5 == 0 and srt_path.is_file() and srt_path.stat().st_size > 0:
            subtitle_rel = srt_path.as_posix()
        else:
            warnings.append("内嵌字幕提取失败：%s" % se5.strip()[-300:])

    # --- 6. evidence.json ---
    evidence = {
        "schema_version": SCHEMA_VERSION,
        "created_at": __import__("datetime").datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": meta,
        "source_url": args.url,
        "extraction": {
            "interval_sec": interval,
            "scene_threshold": float(args.scene),
            "frame_width": width,
            "max_frames": args.max_frames,
            "drawn_timestamps": drawn_timestamps,
            "start_time_offset_sec": meta["start_time_sec"],
        },
        "frames": frames,
        "scene_frames": scene_frames,
        "contact_sheets": sheets,
        "audio": audio_rel,
        "subtitles": subtitle_rel,
        "transcript": None,
        "timeline": None,
        "warnings": warnings,
    }
    evidence_path = out / "evidence.json"
    evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")

    # 清理临时工作目录
    try:
        shutil.rmtree(work_dir)
    except OSError:
        pass

    return emit({
        "ok": True,
        "evidence": evidence_path.as_posix(),
        "frame_count": len(frames),
        "scene_frame_count": len(scene_frames),
        "contact_sheets": [s["file"] for s in sheets],
        "audio": audio_rel,
        "subtitles": subtitle_rel,
        "duration_sec": duration,
        "warnings": warnings,
        "next": [
            "1) 用 ViewImage 逐批看 contact/*.png 与 frames/*.jpg，记录界面与动作",
            "2) 若有 audio.wav：调用 AudioTranscribe 转录，结果存为 transcript.srt / .json / .txt",
            "3) 运行 align --evidence evidence.json --transcript <结果文件>",
        ],
    })


# --------------------------------------------------------------------------- #
# align
# --------------------------------------------------------------------------- #

def _clock_to_seconds(value):
    if isinstance(value, (int, float)):
        v = float(value)
        # 大于 10000 视为毫秒
        return v / 1000.0 if v > 10000 else v
    if not isinstance(value, str):
        return None
    value = value.strip().replace(",", ".")
    parts = value.split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        return float(parts[0])
    except ValueError:
        return None


def _srt_segments(text):
    segments = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if len(lines) < 2:
            continue
        idx = next((i for i, ln in enumerate(lines) if "-->" in ln), None)
        if idx is None:
            continue
        left, right = [p.strip() for p in lines[idx].split("-->", 1)[:2]]
        start, end = _clock_to_seconds(left), _clock_to_seconds(right)
        body = " ".join(lines[idx + 1:])
        speaker = None
        m = re.match(r"^\[?([^\]:：]{1,20})[\]:：]\s*(.+)$", body)
        if m:
            speaker, body = m.group(1).strip(), m.group(2).strip()
        if start is not None and body:
            segments.append({"start": start, "end": end if end is not None else start + 2.0,
                             "text": body, "speaker": speaker})
    return segments


def _pick(item, keys):
    for key in keys:
        if isinstance(item, dict) and item.get(key) is not None:
            return item[key]
    return None


def _json_segments(obj):
    """宽松解析常见 ASR 返回结构；无法识别时返回空列表。"""
    container = None
    if isinstance(obj, list):
        container = obj
    elif isinstance(obj, dict):
        for key in ("segments", "results", "utterances", "items", "sentences", "data", "transcript", "texts"):
            if isinstance(obj.get(key), list):
                container = obj[key]
                break
        if container is None and isinstance(obj.get("result"), dict):
            return _json_segments(obj["result"])
        if container is None:
            for value in obj.values():
                if isinstance(value, list) and value and isinstance(value[0], dict):
                    container = value
                    break
    if not container:
        return []

    segments = []
    for item in container:
        if isinstance(item, str):
            segments.append({"start": None, "end": None, "text": item, "speaker": None})
            continue
        if not isinstance(item, dict):
            continue
        start = _clock_to_seconds(_pick(item, ["start", "begin", "start_time", "from", "offset", "startTime", "bg"]))
        end = _clock_to_seconds(_pick(item, ["end", "finish", "end_time", "to", "endTime", "ed"]))
        text = _pick(item, ["text", "content", "sentence", "transcript", "value", "word", "utterance"])
        speaker = _pick(item, ["speaker", "role", "channel", "spk"])
        if isinstance(text, dict):
            text = _pick(text, ["text", "content", "value"])
        if text is None:
            continue
        segments.append({
            "start": start,
            "end": end,
            "text": str(text).strip(),
            "speaker": str(speaker) if speaker is not None else None,
        })
    return segments


def cmd_align(args):
    evidence_path = Path(args.evidence)
    transcript_path = Path(args.transcript)
    if not evidence_path.is_file():
        return fail("evidence.json 不存在", evidence=str(evidence_path))
    if not transcript_path.is_file():
        return fail("转录结果文件不存在", transcript=str(transcript_path))

    evidence = json.loads(evidence_path.read_text(encoding="utf-8-sig"))
    raw = transcript_path.read_text(encoding="utf-8-sig", errors="replace")
    suffix = transcript_path.suffix.lower()
    warnings = []

    if suffix == ".srt" or suffix == ".vtt":
        segments, mode = _srt_segments(raw), "srt"
    elif suffix == ".json":
        try:
            segments, mode = _json_segments(json.loads(raw)), "json"
        except json.JSONDecodeError as exc:
            return fail("转录 JSON 解析失败", detail=str(exc))
    else:
        segments, mode = [], "plain_text"

    unaligned_text = None
    if mode == "plain_text":
        unaligned_text = raw.strip()
        warnings.append("转录结果无时间戳，无法按时间对齐；全文存于 unaligned_text，需 Agent 人工按顺序分配到步骤")
    if mode in ("srt", "json") and not segments:
        warnings.append("未从转录文件解析出任何片段；检查格式或改用 .txt 纯文本模式")
    missing_ts = sum(1 for s in segments if s["start"] is None)
    if missing_ts:
        warnings.append("%s 个片段缺时间戳，已跳过对齐" % missing_ts)

    timed = [s for s in segments if s["start"] is not None]
    for s in timed:
        if s["end"] is None or s["end"] <= s["start"]:
            s["end"] = s["start"] + 2.0
    timed.sort(key=lambda s: s["start"])

    frames = evidence.get("frames", [])
    timeline_frames = []
    for i, frame in enumerate(frames):
        t0 = as_float(frame.get("time_sec"), 0.0)
        t1 = as_float(frames[i + 1].get("time_sec"), t0 + 1.0) if i + 1 < len(frames) else t0 + 5.0
        hits = []
        for s in timed:
            overlap = min(s["end"], t1) - max(s["start"], t0)
            if overlap > 0:
                hits.append(s)
        speech = " ".join(
            ("[%s] " % s["speaker"] if s.get("speaker") else "") + s["text"] for s in hits
        )
        timeline_frames.append({
            "index": frame.get("index"),
            "time_sec": t0,
            "clock": frame.get("clock") or fmt_clock(t0),
            "file": frame.get("file"),
            "speech": speech.strip(),
            "segment_ids": [timed.index(s) for s in hits],
        })

    out_path = Path(args.out) if args.out else evidence_path.parent / "timeline.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    timeline = {
        "schema_version": SCHEMA_VERSION,
        "evidence": evidence_path.as_posix(),
        "transcript_file": transcript_path.as_posix(),
        "mode": mode,
        "segments": timed,
        "untimed_segments": [s for s in segments if s["start"] is None],
        "unaligned_text": unaligned_text,
        "frames": timeline_frames,
        "warnings": warnings,
    }
    out_path.write_text(json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8")

    evidence["transcript"] = transcript_path.as_posix()
    evidence["timeline"] = out_path.as_posix()
    evidence_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")

    return emit({
        "ok": True,
        "timeline": out_path.as_posix(),
        "mode": mode,
        "segment_count": len(timed),
        "frames_with_speech": sum(1 for f in timeline_frames if f["speech"]),
        "warnings": warnings,
    })


# --------------------------------------------------------------------------- #
# verify
# --------------------------------------------------------------------------- #

def _bigrams(text):
    cleaned = re.sub(r"\s+", "", str(text or ""))
    if len(cleaned) < 2:
        return set(cleaned)
    return {cleaned[i:i + 2] for i in range(len(cleaned) - 1)}


def cmd_verify(args):
    steps_path = Path(args.steps)
    log_path = Path(args.log)
    for label, path in (("steps.json", steps_path), ("replay-log.json", log_path)):
        if not path.is_file():
            return fail("%s 不存在" % label, path=str(path))

    steps_doc = json.loads(steps_path.read_text(encoding="utf-8-sig"))
    log_doc = json.loads(log_path.read_text(encoding="utf-8-sig"))
    steps = steps_doc.get("steps", steps_doc) if isinstance(steps_doc, dict) else steps_doc
    logs = log_doc.get("steps", log_doc) if isinstance(log_doc, dict) else log_doc
    logged = {str(e.get("step_id")): e for e in logs if isinstance(e, dict)}

    items = []
    counts = {"ok": 0, "deviation": 0, "failed": 0, "skipped": 0, "missing": 0, "unknown": 0}
    for step in steps:
        sid = str(step.get("step_id"))
        entry = logged.get(sid)
        required = bool(step.get("required", True))
        if entry is None:
            status = "missing" if required else "skipped"
            items.append({
                "step_id": sid, "status": status, "required": required,
                "intent": step.get("intent"), "expected": step.get("expected"),
                "observed": None, "similarity_hint": None,
                "note": "复现日志中没有该步骤记录",
            })
        else:
            status = str(entry.get("status", "unknown")).lower()
            if status not in counts:
                status = "unknown"
            expected, observed = step.get("expected"), entry.get("observed")
            a, b = _bigrams(expected), _bigrams(observed)
            hint = round(len(a & b) / len(a | b), 3) if (a | b) else None
            items.append({
                "step_id": sid, "status": status, "required": required,
                "intent": step.get("intent"), "expected": expected,
                "observed": observed, "screenshot": entry.get("screenshot"),
                "similarity_hint": hint, "note": entry.get("note"),
            })
        counts[status] = counts.get(status, 0) + 1

    required_total = sum(1 for i in items if i["required"])
    required_ok = sum(1 for i in items if i["required"] and i["status"] == "ok")
    pending = [
        {"step_id": i["step_id"], "status": i["status"], "action": _pending_action(i["status"])}
        for i in items if i["status"] != "ok" and (i["required"] or i["status"] in ("failed", "deviation"))
    ]

    result = {
        "ok": True,
        "steps_file": steps_path.as_posix(),
        "log_file": log_path.as_posix(),
        "summary": {
            "total_steps": len(items),
            "required_steps": required_total,
            "required_ok": required_ok,
            "required_completion_rate": round(required_ok / required_total, 3) if required_total else None,
            "counts": counts,
        },
        "pending_actions": pending,
        "items": items,
        "warnings": [
            "similarity_hint 只是中文二元组重合度的粗略提示，不是验收结论；以截图人工比对和 status 为准",
            "status 由执行者填写，本脚本不读取界面、不判定操作是否真的完成",
        ],
    }
    out_path = Path(args.out) if args.out else log_path.parent / "verification.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return emit({
        "ok": True,
        "verification": out_path.as_posix(),
        "summary": result["summary"],
        "pending_actions": pending,
    })


def _pending_action(status):
    return {
        "missing": "补做该步骤，或在档案中确认是否标为可选",
        "failed": "暂停，检查前置状态与参数；提供重试 / 跳过 / 人工接管三个选项给用户",
        "deviation": "对照档案关键帧与当前截图，确认是有意的参数替换还是执行偏差",
        "skipped": "确认跳过是否影响后续步骤的验收点",
        "unknown": "补全 replay-log.json 中该步骤的 status 字段",
    }.get(status, "人工复核")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def build_parser():
    parser = argparse.ArgumentParser(
        prog="run.py",
        description="视频教程取证与复现工具链（video-tutorial-learner）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("check", help="环境自检")

    p = sub.add_parser("probe", help="读取视频元数据")
    p.add_argument("--input", required=True, help="本地视频路径")

    p = sub.add_parser("fetch", help="下载在线视频")
    p.add_argument("--url", required=True, help="视频页面链接")
    p.add_argument("--out-dir", required=True, help="下载目录")
    p.add_argument("--timeout", type=int, default=900)

    p = sub.add_parser("extract", help="抽帧 / 音轨 / 字幕 / 接触表 / evidence.json")
    p.add_argument("--input", required=True, help="本地视频路径")
    p.add_argument("--out-dir", required=True, help="取证产物目录")
    p.add_argument("--url", default=None, help="原始在线链接（可选，便于溯源）")
    p.add_argument("--interval", type=float, default=3.0, help="固定抽帧间隔秒数，默认 3")
    p.add_argument("--scene", type=float, default=0.35, help="场景切换阈值，默认 0.35")
    p.add_argument("--width", type=int, default=960, help="帧图宽度，默认 960")
    p.add_argument("--max-frames", type=int, default=200, help="固定间隔帧上限，默认 200")
    p.add_argument("--no-scenes", action="store_true", help="跳过场景检测帧")
    p.add_argument("--no-audio", action="store_true", help="跳过音轨提取")

    p = sub.add_parser("align", help="把转录结果对齐到帧时间轴")
    p.add_argument("--evidence", required=True, help="evidence.json 路径")
    p.add_argument("--transcript", required=True, help="ASR 结果：.srt / .json / .txt")
    p.add_argument("--out", default=None, help="输出 timeline.json 路径")

    p = sub.add_parser("verify", help="复现日志与步骤档案比对")
    p.add_argument("--steps", required=True, help="steps.json 路径")
    p.add_argument("--log", required=True, help="replay-log.json 路径")
    p.add_argument("--out", default=None, help="输出 verification.json 路径")

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    handlers = {
        "check": cmd_check,
        "probe": cmd_probe,
        "fetch": cmd_fetch,
        "extract": cmd_extract,
        "align": cmd_align,
        "verify": cmd_verify,
    }
    try:
        return handlers[args.command](args)
    except KeyboardInterrupt:
        return fail("用户中断")
    except Exception as exc:  # noqa: BLE001
        return fail("未预期异常：%s: %s" % (type(exc).__name__, exc))


if __name__ == "__main__":
    sys.exit(main())
