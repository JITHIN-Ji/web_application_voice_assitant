
import logging
from typing import List, Tuple, Dict, Optional
from pipeline.audio_utils import ensure_wav, transcribe_with_deepgram

from pipeline.gemini_llm import query_gemini_summary


class MedicalAudioProcessor:
    def __init__(self, audio_dir: str = "recordings")-> None:
        self.audio_dir = audio_dir

    def ensure_wav(self, audio_path: str) -> str:
        return ensure_wav(audio_path)

    def transcribe_file(self, audio_path: str, beam_size: int = 5):
        # beam_size retained for compatibility; unused by Deepgram
        return transcribe_with_deepgram(audio_path, diarize=True, language="en")

    def query_gemini(self, transcript: str) -> str:
        return query_gemini_summary(transcript)