
import os
import logging
from typing import Optional, List, Dict, Any, Tuple
from agent.config import logger, DEEPGRAM_API_KEY
from deepgram import DeepgramClient

def ensure_wav(audio_path: str) -> str:
    """Convert MP3/M4A/FLAC to WAV if needed"""
    if audio_path.lower().endswith((".mp3", ".m4a", ".flac")):
        temp_dir = os.path.dirname(audio_path)
        wav_path = os.path.join(temp_dir, f"{os.path.splitext(os.path.basename(audio_path))[0]}.wav")

        if os.path.exists(wav_path):
            return wav_path

        try:
            # Lazy-import pydub to avoid hard dependency when conversion is not required
            from pydub import AudioSegment  # type: ignore
            if audio_path.lower().endswith(".mp3"):
                AudioSegment.from_mp3(audio_path).export(wav_path, format="wav")
            elif audio_path.lower().endswith(".m4a"):
                AudioSegment.from_file(audio_path, format="m4a").export(wav_path, format="wav")
            elif audio_path.lower().endswith(".flac"):
                AudioSegment.from_file(audio_path, format="flac").export(wav_path, format="wav")
            
            logger.info(f"Converted {os.path.basename(audio_path)} to {os.path.basename(wav_path)}")
            return wav_path
        except ImportError as ie:
            logger.warning(f"pydub not installed or its dependencies missing (e.g., pyaudioop/ffmpeg): {ie}. Skipping conversion and returning original path.")
        except Exception as e:
            logger.warning(f"Failed to convert {audio_path} to WAV: {e}. Returning original path.")
            return audio_path
    return audio_path

def transcribe_with_deepgram(audio_path: str, diarize: bool = True, language: str = "en") -> Tuple[str, List[Dict[str, Any]]]:
    if not DEEPGRAM_API_KEY:
        logger.error("Deepgram API key not configured. Set DEEPGRAM_API_KEY in environment.")
        return "", []
    try:
        client = DeepgramClient(DEEPGRAM_API_KEY)
        with open(audio_path, "rb") as f:
            buffer = f.read()
        source = {"buffer": buffer}

        dg_model = os.getenv("DEEPGRAM_MODEL", "nova-2-general")
        speaker_count_env = os.getenv("DEEPGRAM_SPEAKER_COUNT")
        diarize_speaker_count: Optional[int] = None
        try:
            if speaker_count_env:
                diarize_speaker_count = int(speaker_count_env)
        except Exception:
            diarize_speaker_count = None

        options = {
            "model": dg_model,
            "smart_format": True,
            "punctuate": True,
            "diarize": diarize,
            "utterances": diarize,
            "language": language
        }
        if diarize and diarize_speaker_count:
            options["diarize_speaker_count"] = diarize_speaker_count

        logger.info(f"Transcribing with Deepgram (diarize={diarize}) -> {os.path.basename(audio_path)}")
        response = client.listen.prerecorded.v("1").transcribe_file(source, options)
        try:
            data = response.to_dict()
        except Exception:
            data = response

        alt = data.get("results", {}).get("channels", [{}])[0].get("alternatives", [{}])[0]
        paragraphs = alt.get("paragraphs", {})
        utterances = data.get("results", {}).get("utterances", [])

        labels_env = os.getenv("DEEPGRAM_SPEAKER_LABELS")
        speaker_labels: Dict[int, str] = {}
        if labels_env:
            try:
                parts = [p.strip() for p in labels_env.split(",") if p.strip()]
                for idx, name in enumerate(parts):
                    speaker_labels[idx] = name
            except Exception:
                speaker_labels = {}
        def format_label(speaker_idx: Optional[int]) -> str:
            if isinstance(speaker_idx, int) and speaker_idx in speaker_labels:
                return speaker_labels[speaker_idx]
            if isinstance(speaker_idx, int):
                return f"Speaker {speaker_idx}"
            return "Speaker"

        transcript_text: Optional[str] = None
        segments: List[Dict[str, Any]] = []
        if diarize and isinstance(utterances, list) and len(utterances) > 0:
            for utt in utterances:
                speaker_idx = utt.get("speaker")
                speaker = format_label(speaker_idx)
                start = utt.get("start")
                end = utt.get("end")
                text = (utt.get("transcript") or "").strip()
                if text:
                    segments.append({"speaker": speaker, "speaker_id": speaker_idx, "start": start, "end": end, "text": text})
            lines = [f"{s['speaker']}: {s['text']}" for s in segments]
            transcript_text = "\n".join(lines).strip()
        elif diarize and isinstance(paragraphs, dict) and paragraphs.get("paragraphs"):
            for para in paragraphs.get("paragraphs", []):
                speaker_idx = para.get("speaker")
                speaker = format_label(speaker_idx)
                start = para.get("start")
                end = para.get("end")
                text = para.get("text", "").strip()
                if text:
                    segments.append({"speaker": speaker, "speaker_id": speaker_idx, "start": start, "end": end, "text": text})
            lines = [f"{s['speaker']}: {s['text']}" for s in segments]
            transcript_text = "\n".join(lines).strip()
        elif diarize and isinstance(alt.get("words"), list):
            words = alt.get("words")
            current_speaker = None
            current_text = []
            seg_start = None
            for w in words:
                sp = w.get("speaker")
                if current_speaker is None:
                    current_speaker = sp
                    seg_start = w.get("start")
                if sp != current_speaker and current_text:
                    segments.append({
                        "speaker": format_label(current_speaker),
                        "speaker_id": current_speaker,
                        "start": seg_start,
                        "end": prev_end,
                        "text": " ".join(current_text).strip()
                    })
                    current_text = []
                    current_speaker = sp
                    seg_start = w.get("start")
                current_text.append(w.get("word", ""))
                prev_end = w.get("end")
            if current_text:
                segments.append({
                    "speaker": format_label(current_speaker),
                    "speaker_id": current_speaker,
                    "start": seg_start,
                    "end": prev_end,
                    "text": " ".join(current_text).strip()
                })

        if not transcript_text:
            transcript_text = (alt.get("transcript") or "").strip()

        def merge_consecutive_segments(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            if not items:
                return items
            merged: List[Dict[str, Any]] = []
            current = dict(items[0])
            for seg in items[1:]:
                if seg.get("speaker") == current.get("speaker") and isinstance(seg.get("start"), (int, float)) and isinstance(current.get("end"), (int, float)):
                    current["end"] = seg.get("end", current.get("end"))
                    if seg.get("text"):
                        current["text"] = (current.get("text", "") + " " + seg["text"]).strip()
                else:
                    merged.append(current)
                    current = dict(seg)
            merged.append(current)
            return merged
        segments = merge_consecutive_segments(segments)

        explicit_map_env = os.getenv("DEEPGRAM_SPEAKER_MAP")
        explicit_map: Dict[int, str] = {}
        if explicit_map_env:
            try:
                pairs = [p.strip() for p in explicit_map_env.split(",") if p.strip()]
                for pair in pairs:
                    if "=" in pair:
                        k, v = pair.split("=", 1)
                        explicit_map[int(k.strip())] = v.strip()
            except Exception:
                explicit_map = {}

        labels_set = set([name.lower() for name in speaker_labels.values()]) if speaker_labels else set()
        wants_doctor_patient = ("doctor" in labels_set and "patient" in labels_set) or (
            isinstance(explicit_map_env, str) and ("Doctor" in explicit_map_env or "Patient" in explicit_map_env)
        )
        if wants_doctor_patient and not explicit_map:
            doctor_keywords = [
                "prescribe", "medication", "take", "dosage", "follow-up", "schedule",
                "diagnosis", "assessment", "plan", "capsule", "tablet", "appointment",
                "antibiotic", "syrup", "recommend", "review", "visit", "emergency"
            ]
            patient_keywords = [
                "i ", "my ", "me ", "feel", "pain", "fever", "cough", "can i", "should i",
                "i'll", "i am", "i have", "symptom", "book"
            ]
            speaker_ids = sorted({s.get("speaker_id") for s in segments if s.get("speaker_id") is not None})
            scores: Dict[int, Dict[str, float]] = {sid: {"doctor": 0.0, "patient": 0.0} for sid in speaker_ids}
            for seg in segments:
                sid = seg.get("speaker_id")
                text_l = (seg.get("text") or "").lower()
                if sid is None:
                    continue
                for kw in doctor_keywords:
                    if kw in text_l:
                        scores[sid]["doctor"] += 1.0
                for kw in patient_keywords:
                    if kw in text_l:
                        scores[sid]["patient"] += 1.0
            mapping: Dict[int, str] = {}
            if len(speaker_ids) == 2:
                sid_a, sid_b = speaker_ids[0], speaker_ids[1]
                a_doctor = scores[sid_a]["doctor"] - scores[sid_a]["patient"]
                b_doctor = scores[sid_b]["doctor"] - scores[sid_b]["patient"]
                if a_doctor == b_doctor:
                    mapping[sid_a] = "Patient"; mapping[sid_b] = "Doctor"
                elif a_doctor > b_doctor:
                    mapping[sid_a] = "Doctor"; mapping[sid_b] = "Patient"
                else:
                    mapping[sid_b] = "Doctor"; mapping[sid_a] = "Patient"
            else:
                for sid in speaker_ids:
                    mapping[sid] = "Doctor" if scores[sid]["doctor"] >= scores[sid]["patient"] else "Patient"
            for s in segments:
                sid = s.get("speaker_id")
                if sid in mapping:
                    s["speaker"] = mapping[sid]
        if explicit_map:
            for s in segments:
                sid = s.get("speaker_id")
                if sid in explicit_map:
                    s["speaker"] = explicit_map[sid]

        return (transcript_text or ""), segments
    except Exception as e:
        logger.error(f"Deepgram transcription failed for {audio_path}: {e}")
        return "", []