#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import imageio_ffmpeg
import numpy as np
from PIL import Image
from rapidocr_onnxruntime import RapidOCR


def configure_utf8_stdio() -> None:
    for name in ("stdin", "stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def utf8_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", required=True, help="Path or URL to a PPT replay video")
    parser.add_argument("--output-dir", required=True, help="Directory for extracted frames and outline files")
    parser.add_argument("--scene-threshold", type=float, default=0.015, help="ffmpeg scene-change threshold")
    parser.add_argument("--min-hash-distance", type=int, default=8, help="Minimum dHash distance to keep a new slide")
    parser.add_argument("--max-slides", type=int, default=80, help="Maximum number of slide frames to keep")
    parser.add_argument("--max-ocr-chars", type=int, default=220, help="Maximum OCR characters kept per slide")
    parser.add_argument("--crop-top-ratio", type=float, default=0.12, help="Crop ratio removed from top before OCR")
    parser.add_argument("--crop-bottom-ratio", type=float, default=0.08, help="Crop ratio removed from bottom before OCR")
    parser.add_argument("--crop-side-ratio", type=float, default=0.03, help="Crop ratio removed from left/right before OCR")
    return parser.parse_args()


def is_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def run_capture(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=utf8_env(),
    )


def compute_dhash(image_path: Path, hash_size: int = 8) -> int:
    img = Image.open(image_path).convert("L").resize((hash_size + 1, hash_size), Image.Resampling.LANCZOS)
    pixels = np.asarray(img, dtype=np.int16)
    diff = pixels[:, 1:] > pixels[:, :-1]
    value = 0
    for bit in diff.flatten():
        value = (value << 1) | int(bit)
    return value


def hamming_distance(left: int, right: int) -> int:
    return (left ^ right).bit_count()


def select_distinct_frames(
    frame_entries: list[dict[str, Any]], min_hash_distance: int, max_slides: int
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    last_hash: int | None = None
    for entry in frame_entries:
        frame_path = Path(entry["path"])
        frame_hash = compute_dhash(frame_path)
        if last_hash is not None and hamming_distance(frame_hash, last_hash) < min_hash_distance:
            continue
        selected.append(
            {
                "path": entry["path"],
                "file_name": frame_path.name,
                "frame_index": entry["frame_index"],
                "timestamp_sec": entry["timestamp_sec"],
                "hash": frame_hash,
            }
        )
        last_hash = frame_hash
        if len(selected) >= max_slides:
            break
    return selected


def extract_scene_frames(video_source: str, frames_dir: Path, scene_threshold: float) -> list[dict[str, Any]]:
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    frames_dir.mkdir(parents=True, exist_ok=True)
    pattern = frames_dir / "slide-%06d.jpg"
    vf = f"select='gt(scene,{scene_threshold})',showinfo"
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "info",
        "-i",
        video_source,
        "-vf",
        vf,
        "-vsync",
        "vfr",
        str(pattern),
    ]
    result = run_capture(cmd)
    frame_paths = sorted(frames_dir.glob("slide-*.jpg"))
    timestamps = [float(value) for value in re.findall(r"pts_time:([0-9.]+)", result.stderr)]
    entries: list[dict[str, Any]] = []
    for idx, frame_path in enumerate(frame_paths):
        entries.append(
            {
                "path": str(frame_path),
                "frame_index": idx + 1,
                "timestamp_sec": round(timestamps[idx], 2) if idx < len(timestamps) else 0.0,
            }
        )
    return entries


def export_selected_frames(selected: list[dict[str, Any]], final_frames_dir: Path) -> list[dict[str, Any]]:
    final_frames_dir.mkdir(parents=True, exist_ok=True)
    exported: list[dict[str, Any]] = []
    for idx, slide in enumerate(selected, start=1):
        src = Path(slide["path"])
        dst = final_frames_dir / f"slide-{idx:03d}.jpg"
        shutil.copy2(src, dst)
        exported.append({**slide, "path": str(dst), "file_name": dst.name})
    return exported


def normalize_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text


def crop_for_ocr(image_path: Path, crop_top_ratio: float, crop_bottom_ratio: float, crop_side_ratio: float) -> np.ndarray:
    image = Image.open(image_path).convert("RGB")
    width, height = image.size
    left = int(width * crop_side_ratio)
    right = width - left
    top = int(height * crop_top_ratio)
    bottom = height - int(height * crop_bottom_ratio)
    cropped = image.crop((left, top, max(left + 1, right), max(top + 1, bottom)))
    return np.asarray(cropped)


def keep_ocr_line(text: str) -> bool:
    if not text:
        return False
    if "C:/" in text or ".pdf" in text or "System32" in text:
        return False
    if re.fullmatch(r"[0-9:/.\- ]{4,}", text):
        return False
    if re.fullmatch(r"\d+/\d+", text):
        return False
    if text in {"应用", "北京航空航天大学"}:
        return False
    if len(text) <= 1:
        return False
    return True


def ocr_frame(
    ocr: RapidOCR,
    image_path: Path,
    max_chars: int,
    crop_top_ratio: float,
    crop_bottom_ratio: float,
    crop_side_ratio: float,
) -> dict[str, Any]:
    cropped = crop_for_ocr(image_path, crop_top_ratio, crop_bottom_ratio, crop_side_ratio)
    result, _ = ocr(cropped)
    lines: list[str] = []
    if result:
        for item in result:
            if len(item) < 2:
                continue
            text = normalize_text(str(item[1]))
            if not keep_ocr_line(text):
                continue
            lines.append(text)
    lines = list(dict.fromkeys(lines))
    merged = normalize_text(" ".join(lines))
    return {
        "ocr_line_count": len(lines),
        "ocr_preview": merged[:max_chars],
        "ocr_lines": lines[:20],
    }


def write_outline(output_dir: Path, slides: list[dict[str, Any]]) -> None:
    lines = ["# PPT 页级提纲", ""]
    for idx, slide in enumerate(slides, start=1):
        lines.append(f"## Slide {idx}")
        lines.append("")
        lines.append(f"- 时间：`{slide['timestamp_sec']}s`")
        lines.append(f"- 帧图：`frames/{slide['file_name']}`")
        preview = slide.get("ocr_preview", "")
        if preview:
            lines.append(f"- OCR 摘要：{preview}")
        else:
            lines.append("- OCR 摘要：未识别到稳定文本")
        detail_lines = slide.get("ocr_lines", [])
        if detail_lines:
            lines.append("- OCR 行：")
            for text in detail_lines[:8]:
                lines.append(f"  - {text}")
        lines.append("")
    (output_dir / "ppt_outline.md").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> None:
    configure_utf8_stdio()
    args = parse_args()
    video_source = args.video
    output_dir = Path(args.output_dir)
    frames_dir = output_dir / "frames"
    temp_frames_dir = output_dir / "_tmp_scene_frames"
    output_dir.mkdir(parents=True, exist_ok=True)

    if not is_url(video_source):
        video_path = Path(video_source)
        if not video_path.exists():
            raise SystemExit(f"Video not found: {video_path}")
        resolved_source = str(video_path)
    else:
        resolved_source = video_source

    shutil.rmtree(temp_frames_dir, ignore_errors=True)
    shutil.rmtree(frames_dir, ignore_errors=True)

    frame_entries = extract_scene_frames(resolved_source, temp_frames_dir, args.scene_threshold)
    selected = select_distinct_frames(frame_entries, args.min_hash_distance, args.max_slides)

    ocr = RapidOCR()
    enriched: list[dict[str, Any]] = []
    for slide in selected:
        ocr_info = ocr_frame(
            ocr,
            Path(slide["path"]),
            args.max_ocr_chars,
            args.crop_top_ratio,
            args.crop_bottom_ratio,
            args.crop_side_ratio,
        )
        if "无视频信号" in ocr_info.get("ocr_preview", ""):
            continue
        enriched.append({**slide, **ocr_info})

    enriched = export_selected_frames(enriched, frames_dir)
    shutil.rmtree(temp_frames_dir, ignore_errors=True)

    (output_dir / "ppt_outline.json").write_text(json.dumps(enriched, ensure_ascii=False, indent=2), encoding="utf-8")
    write_outline(output_dir, enriched)

    print(
        json.dumps(
            {
                "status": "ok",
                "video": resolved_source,
                "scene_frame_count": len(frame_entries),
                "selected_slide_count": len(enriched),
                "outline_json": str(output_dir / "ppt_outline.json"),
                "outline_md": str(output_dir / "ppt_outline.md"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
