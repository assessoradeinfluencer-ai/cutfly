#!/usr/bin/env python3
"""
Extract video scenes based on AI analysis results
Reads scene_analysis JSON and extracts clips with speed adjustments
"""

import argparse
import subprocess
import json
import sys
from pathlib import Path

OUTPUT_DIR = "ai_clips"


def get_export_settings(config):
    export_cfg = config.get("export", {}) if isinstance(config, dict) else {}
    clip_format = export_cfg.get("clip_format", "mp4")
    clip_format = str(clip_format).lower().lstrip('.')
    if clip_format not in {"mkv", "mov", "mp4"}:
        clip_format = "mp4"
    if sys.platform.startswith("darwin"):
        clip_format = "mp4"
    return clip_format, export_cfg


def load_project_config(config_path):
    if not config_path:
        return {}
    path = Path(config_path)
    if not path.exists():
        return {}
    try:
        with open(path, 'r') as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        return {}
    return {}


def format_speed_label(speed):
    return f"{speed:.2f}x"


def extract_scene(video_path, scene, output_path, clip_format="mkv", export_cfg=None):
    """Extrai um trecho simples em H.264 MP4, pensado para rodar bem no macOS."""
    export_cfg = export_cfg or {}
    start_time = scene["start_time"]
    duration = scene["duration"]
    speed = float(scene.get("speed", 1.0) or 1.0)

    probe_cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "a",
        "-show_entries",
        "stream=codec_type",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(video_path),
    ]
    result = subprocess.run(probe_cmd, capture_output=True, text=True)
    has_audio = "audio" in result.stdout.lower()

    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        str(start_time),
        "-t",
        str(duration),
        "-i",
        str(video_path),
    ]

    vf_filters = []
    af_filters = []
    if speed != 1.0:
        vf_filters.append(f"setpts=PTS/{speed}")
        vf_filters.append("fps=24")
        if has_audio:
            factor = speed
            parts = []
            remaining = factor
            while remaining > 2.0:
                parts.append(2.0)
                remaining /= 2.0
            parts.append(remaining)
            af_filters.append(
                ",".join(
                    f"atempo={p:.3f}".rstrip("0").rstrip(".") for p in parts
                )
            )

    if vf_filters:
        cmd += ["-vf", ",".join(vf_filters)]
    if has_audio and af_filters:
        cmd += ["-af", ",".join(af_filters)]

    cmd += [
        "-c:v",
        "libx264",
        "-preset",
        str(export_cfg.get("video_preset", "medium")),
        "-crf",
        str(export_cfg.get("crf", 20)),
    ]

    if has_audio:
        cmd += [
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            "48000",
            "-ac",
            "2",
        ]
    else:
        cmd += ["-an"]

    cmd.append(str(output_path))

    print(f"   Extracting {output_path.name} ({duration:.1f}s @ {speed}x)...", end=" ")
    subprocess.run(cmd, check=True)
    print("✓")


def process_analysis(analysis_file, video_dir, output_base_dir, exclude_boring=False, clip_format="mkv", export_cfg=None):
    with open(analysis_file, 'r') as f:
        analysis = json.load(f)
    
    video_name = analysis.get('video')
    if not video_name:
        print(f"❌ Missing video name in: {analysis_file}")
        return
    
    if video_dir:
        video_path = Path(video_dir) / video_name
    else:
        video_path = Path(analysis_file).parent / video_name
    
    if not video_path.exists():
        print(f"❌ Video file not found: {video_path}")
        return
    
    scenes = analysis['scenes']
    showcases = analysis.get('showcases', [])
    summary = analysis.get('summary', {})
    
    # Filter out boring scenes if requested
    if exclude_boring:
        original_count = len(scenes)
        scenes = [s for s in scenes if s.get('classification') != 'boring']
        if len(scenes) < original_count:
            print(f"\n🚫 Skipping {original_count - len(scenes)} boring scenes (exclude_boring=True)")
    
    output_dir = Path(output_base_dir) / Path(video_name).stem
    output_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"\n🎬 Extracting {len(scenes)} AI-classified scenes from {video_name}...")
    print(f"   Output directory: {output_dir}")

    # Build class counts and speed stats from scenes
    class_counts = {}
    class_speeds = {}
    for scene in scenes:
        classification = scene.get('classification', 'unknown')
        speed = scene.get('speed', 1.0)
        class_counts[classification] = class_counts.get(classification, 0) + 1
        class_speeds.setdefault(classification, []).append(speed)

    print("   Legend (class → speed range):")
    for cls in sorted(class_counts.keys()):
        speeds = class_speeds.get(cls, [])
        if speeds:
            min_speed = min(speeds)
            max_speed = max(speeds)
            avg_speed = sum(speeds) / len(speeds)
            print(f"     {cls:12s} → {format_speed_label(min_speed)}–{format_speed_label(max_speed)} (avg {format_speed_label(avg_speed)})")
        else:
            print(f"     {cls:12s} → n/a")
    print("     skip         → not exported")
    print()
    
    extracted_count = 0
    skipped_count = 0
    
    for scene in scenes:
        scene_num = scene['scene_num']
        classification = scene['classification']
        speed = scene['speed']

        output_name = f"{video_path.stem}_scene_{scene_num:02d}_{classification}_{format_speed_label(speed)}.{clip_format}"
        output_path = output_dir / output_name

        if output_path.exists():
            print(f"   ⏭️  Skipping {output_path.name} (already exists)")
            skipped_count += 1
            continue

        extract_scene(video_path, scene, output_path, clip_format=clip_format, export_cfg=export_cfg)
        extracted_count += 1
    
    # Extract showcase moments (short clips at 1x speed)
    if showcases:
        print(f"\n✨ Extracting {len(showcases)} showcase moments (key highlights at 1x speed)...")
        
        for idx, showcase in enumerate(showcases, 1):
            timestamp = showcase['timestamp']
            # Extract 5 seconds: 2s before + 3s after the timestamp
            start_time = max(0, timestamp - 2)
            duration = 5
            
            output_name = f"{video_path.stem}_showcase_{idx:02d}_{timestamp}s_1.00x.{clip_format}"
            output_path = output_dir / output_name
            
            if output_path.exists():
                print(f"   ⏭️  Skipping {output_path.name} (already exists)")
                skipped_count += 1
                continue
            
            # Create a fake scene object for extraction
            showcase_scene = {
                'start_time': start_time,
                'duration': duration,
                'speed': 1.0
            }
            
            extract_scene(video_path, showcase_scene, output_path, clip_format=clip_format, export_cfg=export_cfg)
            extracted_count += 1
    
    print()
    print("=" * 60)
    print("📊 Extraction Complete")
    print("=" * 60)
    print(f"  Extracted:    {extracted_count} clips")
    print(f"  Skipped:      {skipped_count} clips (already exist)")
    print(f"  Total:        {len(scenes)} scenes + {len(showcases)} showcases")
    print()
    print(f"  Interesting:  {class_counts.get('interesting', summary.get('interesting', 0))} scenes")
    print(f"  Moderate:     {class_counts.get('moderate', summary.get('moderate', 0))} scenes")
    print(f"  Low:          {class_counts.get('low', summary.get('low', 0))} scenes")
    print(f"  Boring:       {class_counts.get('boring', summary.get('boring', 0))} scenes")
    print(f"  Skip:         {class_counts.get('skip', summary.get('skip', 0))} scenes")
    print()
    original_duration = summary.get('original_duration', 0)
    output_duration = summary.get('output_duration', 0)
    compression_ratio = summary.get('compression_ratio', 0)
    print(f"  Original duration:  {original_duration/60:.1f} min")
    print(f"  Output duration:    {output_duration/60:.1f} min")
    print(f"  Compression:        {compression_ratio:.0f}%")
    print("=" * 60)


def main():
    """Extract all scenes from analysis"""
    parser = argparse.ArgumentParser(description="Extract scenes from analysis JSON")
    parser.add_argument("--config", default="project_config.json", help="Project config JSON file")
    parser.add_argument("--analysis", help="Analysis JSON file")
    parser.add_argument("--analysis-dir", help="Directory of analysis JSON files")
    parser.add_argument("--video-dir", help="Directory containing source videos")
    parser.add_argument("--output-dir", default=None, help="Base output directory for clips")
    parser.add_argument("--exclude-boring", action="store_true", help="Skip extraction of boring scenes")
    args = parser.parse_args()

    config = load_project_config(args.config)
    paths_cfg = config.get("paths", {})
    pipeline_cfg = config.get("pipeline", {})
    output_dir = args.output_dir or paths_cfg.get("clips_dir") or OUTPUT_DIR
    video_dir = args.video_dir or paths_cfg.get("video_dir") or paths_cfg.get("input_dir")
    exclude_boring = args.exclude_boring or pipeline_cfg.get("exclude_boring", False)
    clip_format, export_cfg = get_export_settings(config)
    
    analysis_files = []
    if args.analysis_dir:
        analysis_dir = Path(args.analysis_dir)
        analysis_files = sorted(analysis_dir.glob("scene_analysis_*.json"), key=lambda p: p.name.lower())
    elif args.analysis:
        analysis_files = [Path(args.analysis)]
    else:
        analysis_files = [Path("scene_analysis_smart.json")]
    
    analysis_files = [p for p in analysis_files if p.exists()]
    if not analysis_files:
        print("❌ No analysis files found.")
        return
    
    for analysis_file in analysis_files:
        process_analysis(
            analysis_file,
            video_dir,
            output_dir,
            exclude_boring=exclude_boring,
            clip_format=clip_format,
            export_cfg=export_cfg
        )

    print(f"\n💡 Next: run export_resolve.py to build the combined timeline.")


if __name__ == "__main__":
    main()
