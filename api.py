from fastapi import FastAPI, UploadFile, File, BackgroundTasks, Query
from uuid import uuid4
from pathlib import Path
import subprocess
import sys
import json
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from typing import Optional


app = FastAPI()


jobs = {}
projects = {}

def fallback_simple_analysis_local(video_path, output_dir, max_scene_length=40, default_speed=1.0):
import json
from pathlib import Path
scenes = [{
"scene_num": 1, "start_time": 0.0, "duration": 10.0, "speed": float(default_speed), "classification": "moderate"
}]
analysis = {
"video": Path(video_path).name,
"scenes": scenes,
"showcases": [],
"summary": {"original_duration": 10.0, "output_duration": 10.0, "compression_ratio": 0, "interesting": 0, "moderate": 1, "low": 0, "boring": 0, "skip": 0}
}
out_path = Path(output_dir) / f"scene_analysis_{Path(video_path).stem}.json"
with open(out_path, "w") as f:
json.dump(analysis, f, indent=2)
return out_path

def fallback_simple_analysis_local(str(video_path), str(output_dir)):
    output_dir = video_path.parent
    # 1) análise simples gera scene_analysis_*.json
    fallback_simple_analysis(str(video_path), str(output_dir))
    # 2) extrai clipes em MP4 usando extract_scenes.py
    python_executable = sys.executable
    analysis_file = output_dir / f"scene_analysis_{video_path.stem}.json"
    clips_dir = base_dir / "ai_clips"
    cmd = [
        python_executable,
        str(base_dir / "extract_scenes.py"),
        "--analysis",
        str(analysis_file),
        "--video-dir",
        str(output_dir),
        "--output-dir",
        str(clips_dir),
    ]
    subprocess.run(cmd, cwd=str(base_dir), check=True)


def run_pipeline_job(job_id, video_path):
    base_dir = Path(__file__).resolve().parent
    jobs[job_id]["status"] = "running"
    try:
        simple_extract_pipeline(Path(video_path), base_dir)
        jobs[job_id]["return_code"] = 0
        jobs[job_id]["status"] = "completed"
    except Exception as exc:
        jobs[job_id]["return_code"] = 1
        jobs[job_id]["status"] = "failed"
        jobs[job_id]["error"] = str(exc)


@app.post("/jobs")
async def create_job(background_tasks: BackgroundTasks, video: UploadFile = File(...)):
    job_id = str(uuid4())
    base_dir = Path(__file__).resolve().parent
    uploads_dir = base_dir / "assets" / "videos"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(video.filename).suffix or ".mp4"
    video_path = uploads_dir / f"{job_id}{suffix}"
    data = await video.read()
    with open(video_path, "wb") as f:
        f.write(data)
    jobs[job_id] = {
        "status": "queued",
        "video_path": str(video_path),
    }
    background_tasks.add_task(run_pipeline_job, job_id, video_path)
    return {"job_id": job_id, "status": "queued"}


@app.get("/jobs/{job_id}")
def get_job(job_id: str):
    job = jobs.get(job_id)
    if not job:
        return {"error": "job_not_found"}
    return {
        "job_id": job_id,
        "status": job.get("status"),
        "video_path": job.get("video_path"),
        "return_code": job.get("return_code"),
    }


@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/uploads")
async def upload_many(files: list[UploadFile] = File(...)):
    base_dir = Path(__file__).resolve().parent
    uploads_dir = base_dir / "assets" / "videos"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    ids = []
    for file in files:
        fid = str(uuid4())
        suffix = Path(file.filename).suffix or ".mp4"
        dest = uploads_dir / f"{fid}{suffix}"
        data = await file.read()
        with open(dest, "wb") as f:
            f.write(data)
        ids.append({"id": fid, "path": str(dest), "name": file.filename})
    return {"uploaded": ids}

def read_analysis_for_video(video_path: Path):
    base = video_path.parent
    stem = video_path.stem
    analysis_path = base / f"scene_analysis_{stem}.json"
    if not analysis_path.exists():
        return None
    with open(analysis_path, "r") as f:
        return json.load(f)

@app.post("/style-profiles")
async def create_style_profile(video_ids: list[str]):
    base_dir = Path(__file__).resolve().parent
    uploads_dir = base_dir / "assets" / "videos"
    analyses = []
    for vid in video_ids[:3]:
        candidates = list(uploads_dir.glob(f"{vid}.*"))
        if not candidates:
            continue
        video_path = candidates[0]
        analysis = read_analysis_for_video(video_path)
        if analysis is None:
            # run lightweight analysis only (skip extract/export)
            python_executable = sys.executable
            cmd = [
                python_executable,
                str(base_dir / "run_pipeline.py"),
                "--video",
                str(video_path),
                "--skip-extract",
                "--skip-export",
                "--force-analysis",
            ]
            subprocess.run(cmd, cwd=str(base_dir), check=True)
            analysis = read_analysis_for_video(video_path)
        if analysis:
            analyses.append(analysis)
    if not analyses:
        return {"error": "no_analyses"}
    # aggregate simple metrics
    total_scenes = 0
    speed_counts = {}
    class_counts = {}
    for a in analyses:
        for s in a.get("scenes", []):
            total_scenes += 1
            sp = float(s.get("speed", 1.0))
            speed_counts[sp] = speed_counts.get(sp, 0) + 1
            cls = s.get("classification", "unknown")
            class_counts[cls] = class_counts.get(cls, 0) + 1
    avg_speed = sum(k * v for k, v in speed_counts.items()) / max(1, sum(speed_counts.values()))
    jumpcut_level = "alto" if total_scenes >= 40 else "medio" if total_scenes >= 20 else "baixo"
    ritmo = "rapido" if avg_speed >= 2.5 else "moderado" if avg_speed >= 1.5 else "calmo"
    profile_id = str(uuid4())
    profile = {
        "id": profile_id,
        "metrics": {
            "total_scenes": total_scenes,
            "avg_speed": avg_speed,
            "class_counts": class_counts,
            "jumpcut": jumpcut_level,
            "ritmo": ritmo,
        }
    }
    profiles_dir = base_dir / "profiles"
    profiles_dir.mkdir(parents=True, exist_ok=True)
    with open(profiles_dir / f"{profile_id}.json", "w") as f:
        json.dump(profile, f, indent=2)
    return profile

@app.post("/scripts")
async def generate_script(prompt: str, duration_minutes: int = 5, style_profile_id: str = None):
    text = (prompt or "").strip()
    if not text:
        text = "um vídeo curto para redes sociais"
    # Roteiro 100% pronto para leitura, em primeira pessoa
    dur = max(1, min(15, int(duration_minutes or 5)))
    linhas = [
        f"E aí, você sabia que {text}?",
        "Vem comigo explorar esse lugar mágico!",
        "A natureza exuberante abraça a gente logo na entrada. É tanta paz que a gente até esquece do mundo lá fora.",
        "Olha só essas cores e detalhes! Cada cantinho aqui parece ter uma história própria.",
        "E os sons em volta? Dá pra sentir o clima do lugar só de ouvir.",
        "Agora vamos entrar com calma, respeitando a energia e o silêncio desse espaço.",
        "Olha essa vista! Sério, as fotos não fazem justiça. É de tirar o fôlego!",
        "Se você busca tranquilidade e conexão, precisa conhecer esse lugar.",
        "Marca aqui quem vai te acompanhar nessa trip e me siga para mais dicas de lugares incríveis!"
    ]
    full_text = "\n".join(linhas)
    script = {
        "style_profile_id": style_profile_id,
        "duration_minutes": duration_minutes,
        "prompt": text,
        "lines": linhas,
        "full_text": full_text,
    }
    return script

# Static UI
base_dir = Path(__file__).resolve().parent
ui_dir = base_dir / "ui"
ui_dir.mkdir(exist_ok=True)
app.mount("/ui", StaticFiles(directory=str(ui_dir), html=True), name="ui")

def get_results_for_job(job_id: str):
    uploads_dir = base_dir / "assets" / "videos"
    candidates = list(uploads_dir.glob(f"{job_id}.*"))
    if not candidates:
        return None
    video_path = candidates[0]
    stem = video_path.stem
    analysis = uploads_dir / f"scene_analysis_{stem}.json"
    clips_dir = base_dir / "ai_clips" / stem
    timeline = base_dir / "timeline_davinci_resolve.fcpxml"
    clips = []
    if clips_dir.exists():
        for p in sorted(clips_dir.glob("*")):
            if p.is_file():
                clips.append(str(p))
    return {
        "video": str(video_path),
        "analysis": str(analysis) if analysis.exists() else None,
        "clips_dir": str(clips_dir) if clips_dir.exists() else None,
        "clips": clips,
        "timeline": str(timeline) if timeline.exists() else None
    }

@app.get("/results/{job_id}")
def results(job_id: str):
    r = get_results_for_job(job_id)
    if not r:
        return {"error": "not_found"}
    return r

@app.get("/files")
def download(path: str = Query(...)):
    p = Path(path).resolve()
    if not p.exists() or not p.is_file():
        return {"error": "file_not_found"}
    return FileResponse(str(p))

def video_id_to_path(vid: str) -> Optional[Path]:
    base_dir = Path(__file__).resolve().parent
    uploads_dir = base_dir / "assets" / "videos"
    candidates = list(uploads_dir.glob(f"{vid}.*"))
    return candidates[0] if candidates else None

def ensure_analysis_and_clips(video_path: Path, base_dir: Path):
    simple_extract_pipeline(video_path, base_dir)

def get_media_duration(path: Path) -> float:
    cmd = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path)
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return float(out.stdout.strip() or 0.0)
    except Exception:
        return 0.0

def pick_clips_for_lines(video_ids: list[str], base_dir: Path, line_count: int) -> list[Path]:
    def score_from_name(name: str) -> int:
        n = name.lower()
        if "interesting" in n:
            return 3
        if "moderate" in n:
            return 2
        if "low" in n:
            return 1
        return 0
    all_clips: list[Path] = []
    uploads_dir = base_dir / "assets" / "videos"
    for vid in video_ids:
        vpath = video_id_to_path(vid)
        if not vpath:
            continue
        stem = vpath.stem
        clips_dir = base_dir / "ai_clips" / stem
        if clips_dir.exists():
            clips = sorted([p for p in clips_dir.glob("*.mp4") if p.is_file()],
                           key=lambda p: (-score_from_name(p.name), p.name))
            all_clips.extend(clips)
    if not all_clips:
        return []
    # ciclo através dos melhores clipes para cobrir todas as linhas
    chosen = []
    i = 0
    while len(chosen) < line_count:
        chosen.append(all_clips[i % len(all_clips)])
        i += 1
    return chosen

def build_srt(lines: list[str], total_duration: float, out_path: Path):
    words_per_line = [max(1, len(l.split())) for l in lines]
    total_words = sum(words_per_line)
    # distribuição proporcional pelo número de palavras
    durations = [total_duration * (w / total_words) for w in words_per_line]
    def fmt_ts(sec: float) -> str:
        h = int(sec // 3600); sec -= h*3600
        m = int(sec // 60); sec -= m*60
        s = int(sec); ms = int((sec - s) * 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
    t = 0.0
    with open(out_path, "w") as f:
        for idx, (line, d) in enumerate(zip(lines, durations), start=1):
            start = t
            end = t + d
            f.write(f"{idx}\n")
            f.write(f"{fmt_ts(start)} --> {fmt_ts(end)}\n")
            f.write(line.strip() + "\n\n")
            t = end

@app.post("/narrations")
async def upload_narration(file: UploadFile = File(...)):
    base_dir = Path(__file__).resolve().parent
    narr_dir = base_dir / "assets" / "narrations"
    narr_dir.mkdir(parents=True, exist_ok=True)
    fid = str(uuid4())
    suffix = Path(file.filename).suffix or ".webm"
    dest = narr_dir / f"{fid}{suffix}"
    data = await file.read()
    with open(dest, "wb") as f:
        f.write(data)
    return {"id": fid, "path": str(dest)}

def try_run_advanced_analysis(video_path: Path, base_dir: Path):
    metadata_file = video_path.parent / f"metadata_{video_path.stem}.json"
    scene_file = video_path.parent / f"scene_analysis_{video_path.stem}.json"
    if metadata_file.exists() and scene_file.exists():
        return
    python_executable = sys.executable
    try:
        cmd = [
            python_executable,
            str(base_dir / "analyze_advanced5.py"),
            "--video",
            str(video_path),
            "--output-dir",
            str(video_path.parent)
        ]
        subprocess.run(cmd, cwd=str(base_dir), check=True)
    except Exception:
        # deixa fallback cuidar; não quebra
        pass

def load_metadata(video_path: Path):
    p = video_path.parent / f"metadata_{video_path.stem}.json"
    if not p.exists():
        return None
    with open(p, "r") as f:
        return json.load(f)

def _tokens(text: str):
    return [t for t in "".join([c if c.isalnum() or c.isspace() else " " for c in text.lower()]).split() if len(t) > 2]

def score_frames_by_line(frames: list, line: str):
    tline = set(_tokens(line))
    best = None
    for fr in frames:
        cap = fr.get("caption") or ""
        inter = float(fr.get("semantic_interest", 0.0))
        toks = set(_tokens(cap))
        overlap = len(tline & toks)
        score = overlap * 0.7 + inter * 0.3
        ts = float(fr.get("timestamp", 0.0))
        if best is None or score > best[0]:
            best = (score, ts)
    return best[1] if best else None

def choose_frame_for_line(video_ids: list[str], base_dir: Path, line: str) -> tuple[Optional[Path], Optional[float]]:
    for vid in video_ids:
        vpath = video_id_to_path(vid)
        if not vpath:
            continue
        try_run_advanced_analysis(vpath, base_dir)
        md = load_metadata(vpath)
        if not md:
            continue
        ts = score_frames_by_line(md.get("frames", []), line)
        if ts is not None:
            return vpath, ts
    return None, None

def cut_segment_from_source(src: Path, start_ts: float, duration: float, out: Path, fade_seconds: float = 0.0):
    vf = []
    if fade_seconds and duration > (fade_seconds * 2 + 0.2):
        st_out = max(0.0, duration - fade_seconds)
        vf.append(f"fade=t=in:st=0:d={fade_seconds:.2f}")
        vf.append(f"fade=t=out:st={st_out:.2f}:d={fade_seconds:.2f}")
    vf_arg = []
    if vf:
        vf_arg = ["-vf", ",".join(vf)]
    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{max(0.0, start_ts):.3f}",
        "-t", f"{max(0.5, duration):.3f}",
        "-i", str(src),
        *vf_arg,
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-c:a", "aac", "-b:a", "128k",
        str(out)
    ]
    subprocess.run(cmd, check=True)

def run_project_job(project_id: str, video_ids: list[str], script: Optional[dict]):
    base_dir = Path(__file__).resolve().parent
    projects_dir = base_dir / "projects" / project_id
    projects_dir.mkdir(parents=True, exist_ok=True)
    projects[project_id] = {"status": "running", "output": None}
    try:
        # 1) garantir clipes para todos os vídeos
        for vid in video_ids:
            vpath = video_id_to_path(vid)
            if vpath:
                ensure_analysis_and_clips(vpath, base_dir)
        # 2) obter narração (se enviada) e duração total
        narration_path = None
        if script and isinstance(script, dict):
            narration_path = script.get("narration_path")
        lines = []
        if script and isinstance(script, dict):
            if isinstance(script.get("lines"), list) and script.get("lines"):
                lines = [str(x) for x in script["lines"]]
            elif script.get("full_text"):
                lines = [p.strip() for p in str(script["full_text"]).split("\n") if p.strip()]
        if not lines:
            lines = ["Este é um vídeo de teste gerado automaticamente."]
        if narration_path:
            total_dur = get_media_duration(Path(narration_path))
        else:
            # estimar pela contagem de palavras (145 wpm)
            wpm = 145.0
            total_words = sum(len(l.split()) for l in lines)
            total_dur = (total_words / wpm) * 60.0
        # ajustar por perfil de estilo, se existir
        ritmo_multiplier = 1.0
        style_id = script.get("style_profile_id") if isinstance(script, dict) else None
        if style_id:
            profiles_dir = base_dir / "profiles"
            prof_path = profiles_dir / f"{style_id}.json"
            if prof_path.exists():
                with open(prof_path, "r") as pf:
                    prof = json.load(pf)
                ritmo = (prof.get("metrics", {}).get("ritmo") or "").lower()
                if ritmo == "rapido":
                    ritmo_multiplier = 0.85
                elif ritmo == "calmo":
                    ritmo_multiplier = 1.15
        total_dur *= ritmo_multiplier
        # 3) calcular duração por linha proporcional às palavras e alinhar em batidas de 0.5s
        words_per_line = [max(1, len(l.split())) for l in lines]
        total_words = sum(words_per_line)
        base_durations = [total_dur * (w / total_words) for w in words_per_line]
        durations = [max(0.6, round(d * 2) / 2.0) for d in base_durations]
        # 4) escolher para cada linha um timestamp semântico (se possível) ou fallback
        srt_path = projects_dir / "subtitles.srt"
        build_srt(lines, sum(durations), srt_path)
        # calcular duração por linha igual ao SRT
        # vamos reler os tempos do srt gerado para saber cada duração
        durations = []
        with open(srt_path, "r") as f:
            content = f.read().strip().split("\n\n")
        for block in content:
            parts = block.splitlines()
            if len(parts) >= 2 and "-->" in parts[1]:
                t1, t2 = parts[1].split("-->")
                def parse_ts(ts):
                    h, m, rest = ts.strip().split(":")
                    s, ms = rest.split(",")
                    return int(h)*3600 + int(m)*60 + int(s) + int(ms)/1000.0
                durations.append(parse_ts(t2) - parse_ts(t1))
        seg_dir = projects_dir / "segments"
        seg_dir.mkdir(exist_ok=True)
        segment_paths = []
        # define fade conforme estilo
        fade_sec = 0.0
        if style_id:
            profiles_dir = base_dir / "profiles"
            prof_path = profiles_dir / f"{style_id}.json"
            if prof_path.exists():
                with open(prof_path, "r") as pf:
                    prof = json.load(pf)
                ritmo = (prof.get("metrics", {}).get("ritmo") or "").lower()
                if ritmo == "calmo":
                    fade_sec = 0.18
                elif ritmo == "moderado":
                    fade_sec = 0.10
                else:
                    fade_sec = 0.0
        for idx, (line, dur) in enumerate(zip(lines, durations), start=1):
            src, ts = choose_frame_for_line(video_ids, base_dir, line)
            seg_out = seg_dir / f"seg_{idx:02d}.mp4"
            if src and ts is not None:
                cut_segment_from_source(src, ts, dur, seg_out, fade_seconds=fade_sec)
            else:
                # fallback: usa primeiro clipe disponível
                candidates = pick_clips_for_lines(video_ids, base_dir, 1)
                if candidates:
                    cmd = [
                        "ffmpeg", "-y",
                        "-t", f"{max(0.8, dur):.3f}",
                        "-i", str(candidates[0]),
                        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
                        "-c:a", "aac", "-b:a", "128k",
                        str(seg_out)
                    ]
                    subprocess.run(cmd, cwd=str(base_dir), check=True)
                else:
                    # gera um quadro de cor sólida para não quebrar
                    cmd = [
                        "ffmpeg", "-y",
                        "-f", "lavfi", "-i", "color=c=black:s=640x360:r=24",
                        "-t", f"{max(0.8, dur):.3f}",
                        str(seg_out)
                    ]
                    subprocess.run(cmd, cwd=str(base_dir), check=True)
            segment_paths.append(seg_out)
        concat_file = projects_dir / "concat.txt"
        with open(concat_file, "w") as f:
            for p in segment_paths:
                f.write(f"file '{str(p)}'\n")
        temp_video = projects_dir / "temp_video.mp4"
        cmd_concat = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(concat_file),
            "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-c:a", "aac", "-b:a", "192k",
            str(temp_video)
        ]
        subprocess.run(cmd_concat, cwd=str(base_dir), check=True)
        # 5) aplicar narração (se existir)
        with_audio = temp_video
        if narration_path:
            with_audio = projects_dir / "with_audio.mp4"
            cmd_audio = [
                "ffmpeg", "-y",
                "-i", str(temp_video),
                "-i", str(narration_path),
                "-map", "0:v", "-map", "1:a",
                "-shortest",
                "-c:v", "copy",
                "-c:a", "aac", "-b:a", "192k",
                str(with_audio)
            ]
            subprocess.run(cmd_audio, cwd=str(base_dir), check=True)
        # 6) queimar legendas (se suportado), senão entregar srt separado
        final_mp4 = projects_dir / "output.mp4"
        try:
            cmd_sub = [
                "ffmpeg", "-y",
                "-i", str(with_audio),
                "-vf", f"subtitles={str(srt_path)}",
                "-c:v", "libx264", "-preset", "medium", "-crf", "20",
                "-c:a", "aac", "-b:a", "192k",
                str(final_mp4)
            ]
            subprocess.run(cmd_sub, cwd=str(base_dir), check=True)
        except Exception:
            # fallback: sem queima, apenas copia e informa srt
            final_mp4 = with_audio
        projects[project_id]["status"] = "completed"
        projects[project_id]["output"] = str(final_mp4)
        projects[project_id]["subtitles"] = str(srt_path)
    except Exception:
        projects[project_id]["status"] = "failed"

@app.post("/projects")
async def create_project(background_tasks: BackgroundTasks, payload: dict):
    video_ids = payload.get("video_ids") or []
    script = payload.get("script")
    # permitir passar caminho da narração separadamente
    narration_path = payload.get("narration_path")
    if script and narration_path:
        script["narration_path"] = narration_path
    project_id = str(uuid4())
    projects[project_id] = {"status": "queued", "output": None}
    background_tasks.add_task(run_project_job, project_id, video_ids, script)
    return {"project_id": project_id, "status": "queued"}

@app.get("/projects/{project_id}")
def get_project(project_id: str):
    data = projects.get(project_id)
    if not data:
        return {"error": "project_not_found"}
    return {"project_id": project_id, **data}
