#!/usr/bin/env python3
"""Batch-generate interview audios and SadTalker videos from a Q/A text file.

Pipeline:
1) Extract question text from lines like: "1. Q: ..."
2) Generate one VoiceRSS audio file per question
3) Run SadTalker once per audio to create one talking-head video per question

Outputs are written under generated/interview_batch_<timestamp>/
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import List

DEFAULT_VOICERSS_KEY = "759c79c9515242148848e58daaf0d74c"
QUESTION_PATTERN = re.compile(r"^\s*\d+\.\s*Q:\s*(.+?)\s*$", re.IGNORECASE)
PLAIN_Q_PATTERN = re.compile(r"^\s*Q:\s*(.+?)\s*$", re.IGNORECASE)


def parse_questions(qa_file: Path) -> List[str]:
    questions: List[str] = []
    with qa_file.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue

            match = QUESTION_PATTERN.match(line)
            if not match:
                match = PLAIN_Q_PATTERN.match(line)
            if not match:
                continue

            question = match.group(1).strip()
            if question:
                questions.append(question)

    unique_questions = []
    seen = set()
    for q in questions:
        if q not in seen:
            unique_questions.append(q)
            seen.add(q)
    return unique_questions


def synthesize_voicerss(text: str, out_audio: Path, api_key: str, lang: str = "en-us", codec: str = "MP3") -> None:
    params = urllib.parse.urlencode(
        {
            "key": api_key,
            "hl": lang,
            "src": text,
            "c": codec,
            "f": "44khz_16bit_stereo",
        }
    ).encode("utf-8")

    request = urllib.request.Request(
        "https://api.voicerss.org/",
        data=params,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            data = response.read()
            content_type = (response.headers.get("Content-Type") or "").lower()
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"VoiceRSS HTTP error {error.code}: {error.reason}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"VoiceRSS network error: {error.reason}") from error

    # VoiceRSS sends plain text like "ERROR: ..." on failures.
    if "text/plain" in content_type or "application/json" in content_type:
        body_text = data.decode("utf-8", errors="ignore")
        if body_text.strip().upper().startswith("ERROR"):
            raise RuntimeError(f"VoiceRSS error: {body_text.strip()}")

    out_audio.write_bytes(data)


def find_new_video(result_dir: Path, before_files: set[Path]) -> Path:
    after_files = set(result_dir.glob("*.mp4"))
    created = sorted(after_files - before_files, key=lambda p: p.stat().st_mtime)
    if created:
        return created[-1]

    if after_files:
        return sorted(after_files, key=lambda p: p.stat().st_mtime)[-1]

    raise RuntimeError(f"No mp4 found in SadTalker result dir: {result_dir}")


def run_sadtalker(
    sadtalker_python: str,
    sadtalker_dir: Path,
    source_image: Path,
    audio_path: Path,
    result_dir: Path,
    use_cpu: bool,
    use_still: bool,
    preprocess: str,
) -> Path:
    inference_py = sadtalker_dir / "inference.py"
    if not inference_py.exists():
        raise FileNotFoundError(f"Missing SadTalker inference file: {inference_py}")

    result_dir.mkdir(parents=True, exist_ok=True)
    before_mp4 = set(result_dir.glob("*.mp4"))

    cmd = [
        sadtalker_python,
        str(inference_py),
        "--driven_audio",
        str(audio_path),
        "--source_image",
        str(source_image),
        "--result_dir",
        str(result_dir),
        "--preprocess",
        preprocess,
    ]
    if use_still:
        cmd.append("--still")
    if use_cpu:
        cmd.append("--cpu")

    process = subprocess.run(cmd, cwd=sadtalker_dir, text=True)
    if process.returncode != 0:
        raise RuntimeError(f"SadTalker failed with exit code {process.returncode} for {audio_path.name}")

    return find_new_video(result_dir, before_mp4)


def build_sequence(items: List[dict], static_image: str) -> List[dict]:
    sequence: List[dict] = []
    for idx, item in enumerate(items):
        sequence.append(
            {
                "type": "video",
                "index": item["index"],
                "question": item["question"],
                "video": item["video_file"],
                "audio": item["audio_file"],
            }
        )
        if idx < len(items) - 1:
            sequence.append({"type": "image", "image": static_image})
    return sequence


def load_audio_only_items(audio_dir: Path, limit: int) -> List[dict]:
    audio_files = sorted(
        [p for p in audio_dir.iterdir() if p.is_file() and p.suffix.lower() in {".mp3", ".wav", ".aac", ".ogg", ".caf"}],
        key=lambda p: p.name.lower(),
    )
    if limit > 0:
        audio_files = audio_files[:limit]

    items: List[dict] = []
    for idx, audio_path in enumerate(audio_files, start=1):
        items.append(
            {
                "index": idx,
                "qid": f"q{idx:03d}",
                "question": f"Question {idx}",
                "audio_path": audio_path,
            }
        )
    return items


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate question-wise TTS + SadTalker videos in batch.")
    parser.add_argument(
        "--qa-file",
        default="vlsi_interview_qa.txt",
        help="Path to text file containing numbered Q/A entries.",
    )
    parser.add_argument(
        "--sadtalker-dir",
        default="SadTalker",
        help="Path to SadTalker repository root.",
    )
    parser.add_argument(
        "--source-image",
        default="SadTalker/pic/mohi_didi.png",
        help="Portrait image used by SadTalker.",
    )
    parser.add_argument(
        "--sadtalker-python",
        default=sys.executable,
        help="Python executable that has SadTalker dependencies installed.",
    )
    parser.add_argument(
        "--voice-key",
        default=os.getenv("VOICERSS_API_KEY", DEFAULT_VOICERSS_KEY),
        help="VoiceRSS API key.",
    )
    parser.add_argument("--voice-lang", default="en-us", help="VoiceRSS language code.")
    parser.add_argument("--voice-codec", default="MP3", choices=["MP3", "WAV", "AAC", "OGG", "CAF"], help="VoiceRSS codec.")
    parser.add_argument("--output-root", default="generated", help="Root output directory.")
    parser.add_argument("--preprocess", default="crop", choices=["crop", "extcrop", "resize", "full", "extfull"], help="SadTalker preprocess mode.")
    parser.add_argument("--cpu", action="store_true", help="Force SadTalker CPU mode.")
    parser.add_argument("--no-still", action="store_true", help="Disable SadTalker --still flag.")
    parser.add_argument("--audio-only", action="store_true", help="Generate all question audios and skip SadTalker video generation.")
    parser.add_argument("--audio-dir", default="", help="Use pre-generated audio files from this folder instead of TTS generation.")
    parser.add_argument(
        "--continue-on-video-error",
        action="store_true",
        help="Keep processing remaining questions if SadTalker fails for one question.",
    )
    parser.add_argument("--limit", type=int, default=0, help="Optional max number of questions to process. 0 = all.")
    parser.add_argument(
        "--static-image",
        default="public/interviewer.svg",
        help="Image path to use in generated playback sequence between videos.",
    )

    args = parser.parse_args()

    qa_file = Path(args.qa_file).resolve()
    sadtalker_dir = Path(args.sadtalker_dir).resolve()
    source_image = Path(args.source_image).resolve()
    output_root = Path(args.output_root).resolve()
    source_audio_dir = Path(args.audio_dir).resolve() if args.audio_dir else None

    if not qa_file.exists():
        raise FileNotFoundError(f"Q/A file not found: {qa_file}")
    if not sadtalker_dir.exists():
        raise FileNotFoundError(f"SadTalker dir not found: {sadtalker_dir}")
    if not source_image.exists():
        raise FileNotFoundError(f"Source image not found: {source_image}")
    if not args.voice_key:
        raise ValueError("VoiceRSS API key is empty. Set VOICERSS_API_KEY or pass --voice-key.")

    run_stamp = datetime.now().strftime("interview_batch_%Y%m%d_%H%M%S")
    run_dir = output_root / run_stamp
    audio_dir = run_dir / "audio"
    video_raw_dir = run_dir / "sadtalker_raw"
    final_video_dir = run_dir / "videos"

    audio_dir.mkdir(parents=True, exist_ok=True)
    video_raw_dir.mkdir(parents=True, exist_ok=True)
    final_video_dir.mkdir(parents=True, exist_ok=True)

    questions: List[str] = []
    audio_items: List[dict] = []
    if source_audio_dir:
        if not source_audio_dir.exists():
            raise FileNotFoundError(f"Audio dir not found: {source_audio_dir}")
        audio_items = load_audio_only_items(source_audio_dir, args.limit)
        if not audio_items:
            raise RuntimeError(f"No supported audio files found in: {source_audio_dir}")
    else:
        questions = parse_questions(qa_file)
        if not questions:
            raise RuntimeError("No questions found. Expected lines like '1. Q: ...'")
        if args.limit > 0:
            questions = questions[: args.limit]

        codec_ext = args.voice_codec.lower()
        for idx, question in enumerate(questions, start=1):
            qid = f"q{idx:03d}"
            audio_items.append(
                {
                    "index": idx,
                    "qid": qid,
                    "question": question,
                    "audio_path": audio_dir / f"{qid}.{codec_ext}",
                }
            )

    if source_audio_dir:
        print(f"Found {len(audio_items)} audio file(s) in {source_audio_dir}")
    else:
        print(f"Found {len(questions)} question(s) in {qa_file}")
    print(f"Run output directory: {run_dir}")

    results: List[dict] = []
    for item in audio_items:
        idx = item["index"]
        qid = item["qid"]
        question = item["question"]
        audio_file = item["audio_path"]
        per_q_result_dir = video_raw_dir / qid

        print("=" * 80)
        print(f"[{idx}/{len(audio_items)}] {question}")

        if source_audio_dir:
            print(f"Using existing audio -> {audio_file.name}")
        else:
            print(f"Generating TTS -> {audio_file.name}")
            synthesize_voicerss(question, audio_file, args.voice_key, lang=args.voice_lang, codec=args.voice_codec)

        final_video_rel = None
        generated_video = None
        if args.audio_only:
            print("Skipping SadTalker (--audio-only enabled)")
        else:
            print("Running SadTalker...")
            try:
                generated_video = run_sadtalker(
                    sadtalker_python=args.sadtalker_python,
                    sadtalker_dir=sadtalker_dir,
                    source_image=source_image,
                    audio_path=audio_file,
                    result_dir=per_q_result_dir,
                    use_cpu=args.cpu,
                    use_still=not args.no_still,
                    preprocess=args.preprocess,
                )

                final_video = final_video_dir / f"video_{idx:03d}.mp4"
                shutil.copy2(generated_video, final_video)
                final_video_rel = str(final_video.relative_to(run_dir)).replace("\\", "/")
                print(f"Saved final video -> {final_video.name}")
            except Exception as video_exc:
                if not args.continue_on_video_error:
                    raise
                print(f"Warning: SadTalker failed for {qid}: {video_exc}")

        try:
            audio_rel = str(audio_file.relative_to(run_dir)).replace("\\", "/")
        except ValueError:
            audio_rel = str(audio_file)

        results.append(
            {
                "index": idx,
                "question": question,
                "audio_file": audio_rel,
                "video_file": final_video_rel,
                "raw_sadtalker_video": str(generated_video) if generated_video else None,
            }
        )

        # Short pause helps avoid transient API throttling on free tiers.
        time.sleep(0.2)

    manifest = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "qa_file": str(qa_file),
        "source_image": str(source_image),
        "question_count": len(results),
        "items": results,
        "sequence": build_sequence([item for item in results if item.get("video_file")], args.static_image),
    }

    manifest_path = run_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print("=" * 80)
    print("Batch generation complete.")
    print(f"Manifest: {manifest_path}")
    print(f"Videos:   {final_video_dir}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
