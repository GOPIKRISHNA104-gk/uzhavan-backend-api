"""
Emotion Detection Engine for Voice AI
Analyzes audio features to detect farmer's emotional state.
Uses pitch, energy, MFCC, and tone variation analysis.
Supports all 6 languages (emotion is language-agnostic in audio features).
"""

import numpy as np
import struct
import math
from typing import Dict, Any, Optional, List
from enum import Enum


class Emotion(str, Enum):
    WORRIED = "worried"
    ANGRY = "angry"
    CONFUSED = "confused"
    HAPPY = "happy"
    NEUTRAL = "neutral"


# Emotion-specific AI behavior instructions
EMOTION_PROMPTS: Dict[str, str] = {
    Emotion.WORRIED: (
        "The farmer sounds worried or anxious. "
        "Use a calm, reassuring tone. "
        "Offer practical solutions with confidence. "
        "Say things like 'Don't worry, here is what you can do...'"
    ),
    Emotion.ANGRY: (
        "The farmer sounds frustrated or angry. "
        "Be very polite and respectful. "
        "Speak at a slower pace. "
        "Acknowledge their frustration first before providing solutions."
    ),
    Emotion.CONFUSED: (
        "The farmer sounds confused or uncertain. "
        "Break your answer into simple numbered steps. "
        "Use very simple language with practical examples. "
        "Ask if they need more clarification at the end."
    ),
    Emotion.HAPPY: (
        "The farmer sounds happy or excited. "
        "Match their positive energy. "
        "Celebrate their success if applicable. "
        "Provide enthusiastic, encouraging responses."
    ),
    Emotion.NEUTRAL: (
        "The farmer is speaking normally. "
        "Respond in a friendly, helpful manner. "
        "Be concise and informative."
    ),
}


class EmotionDetector:
    """
    Lightweight emotion detection from raw audio features.
    Uses signal processing (no ML model required).
    
    Features analyzed:
    - RMS Energy (volume/intensity)
    - Zero Crossing Rate (pitch proxy)
    - Spectral Centroid (brightness)
    - Energy Variance (stability)
    """

    def __init__(self):
        # Thresholds tuned for speech emotion detection
        self.high_energy_threshold = 0.15
        self.low_energy_threshold = 0.03
        self.high_zcr_threshold = 0.15
        self.low_zcr_threshold = 0.05
        self.high_variance_threshold = 0.02

    def analyze_audio_bytes(self, audio_bytes: bytes, sample_rate: int = 16000) -> Dict[str, Any]:
        """
        Analyze raw PCM audio bytes for emotion.
        Expects 16-bit PCM audio.
        """
        try:
            # Convert bytes to float samples
            samples = self._bytes_to_samples(audio_bytes)
            
            if len(samples) < sample_rate * 0.5:  # Need at least 0.5s
                return {
                    "emotion": Emotion.NEUTRAL,
                    "confidence": 0.3,
                    "prompt_injection": EMOTION_PROMPTS[Emotion.NEUTRAL],
                    "features": {}
                }
            
            # Extract features
            features = self._extract_features(samples, sample_rate)
            
            # Classify emotion
            emotion, confidence = self._classify_emotion(features)
            
            return {
                "emotion": emotion,
                "confidence": round(confidence, 2),
                "prompt_injection": EMOTION_PROMPTS[emotion],
                "features": {
                    "rms_energy": round(features["rms_energy"], 4),
                    "zcr": round(features["zcr"], 4),
                    "energy_variance": round(features["energy_variance"], 6),
                    "spectral_centroid": round(features["spectral_centroid"], 2),
                    "speech_rate_proxy": round(features["speech_rate_proxy"], 4),
                }
            }
        except Exception as e:
            print(f"Emotion detection error: {e}")
            return {
                "emotion": Emotion.NEUTRAL,
                "confidence": 0.0,
                "prompt_injection": EMOTION_PROMPTS[Emotion.NEUTRAL],
                "features": {}
            }

    def _bytes_to_samples(self, audio_bytes: bytes) -> np.ndarray:
        """Convert raw PCM 16-bit bytes to float32 numpy array [-1.0, 1.0]"""
        # Ensure even number of bytes
        n_bytes = len(audio_bytes) - (len(audio_bytes) % 2)
        n_samples = n_bytes // 2
        
        samples = struct.unpack(f'<{n_samples}h', audio_bytes[:n_bytes])
        return np.array(samples, dtype=np.float32) / 32768.0

    def _extract_features(self, samples: np.ndarray, sample_rate: int) -> Dict[str, float]:
        """Extract audio features for emotion classification"""
        
        # 1. RMS Energy (overall loudness)
        rms_energy = float(np.sqrt(np.mean(samples ** 2)))
        
        # 2. Zero Crossing Rate (pitch/frequency proxy)
        zero_crossings = np.sum(np.abs(np.diff(np.sign(samples)))) / 2
        zcr = float(zero_crossings / len(samples))
        
        # 3. Energy Variance (stability of voice)
        frame_size = int(sample_rate * 0.025)  # 25ms frames
        hop_size = int(sample_rate * 0.010)     # 10ms hop
        
        frame_energies = []
        for i in range(0, len(samples) - frame_size, hop_size):
            frame = samples[i:i + frame_size]
            frame_energies.append(float(np.sqrt(np.mean(frame ** 2))))
        
        energy_variance = float(np.var(frame_energies)) if frame_energies else 0.0
        
        # 4. Spectral Centroid (brightness/sharpness)
        fft = np.fft.rfft(samples)
        magnitude = np.abs(fft)
        freqs = np.fft.rfftfreq(len(samples), d=1.0/sample_rate)
        
        if np.sum(magnitude) > 0:
            spectral_centroid = float(np.sum(freqs * magnitude) / np.sum(magnitude))
        else:
            spectral_centroid = 0.0
        
        # 5. Speech Rate Proxy (energy peaks per second)
        # More peaks = faster speech
        if frame_energies:
            energy_arr = np.array(frame_energies)
            threshold = np.mean(energy_arr) * 0.5
            peaks = 0
            above = False
            for e in energy_arr:
                if e > threshold and not above:
                    peaks += 1
                    above = True
                elif e <= threshold:
                    above = False
            duration = len(samples) / sample_rate
            speech_rate_proxy = peaks / duration if duration > 0 else 0
        else:
            speech_rate_proxy = 0.0
        
        return {
            "rms_energy": rms_energy,
            "zcr": zcr,
            "energy_variance": energy_variance,
            "spectral_centroid": spectral_centroid,
            "speech_rate_proxy": speech_rate_proxy,
        }

    def _classify_emotion(self, features: Dict[str, float]) -> tuple:
        """
        Rule-based emotion classification from audio features.
        Returns (emotion, confidence)
        """
        energy = features["rms_energy"]
        zcr = features["zcr"]
        variance = features["energy_variance"]
        centroid = features["spectral_centroid"]
        speech_rate = features["speech_rate_proxy"]
        
        scores: Dict[Emotion, float] = {
            Emotion.NEUTRAL: 0.3,  # Base score
            Emotion.WORRIED: 0.0,
            Emotion.ANGRY: 0.0,
            Emotion.CONFUSED: 0.0,
            Emotion.HAPPY: 0.0,
        }
        
        # ANGRY: high energy, high ZCR, high spectral centroid
        if energy > self.high_energy_threshold:
            scores[Emotion.ANGRY] += 0.3
        if zcr > self.high_zcr_threshold:
            scores[Emotion.ANGRY] += 0.2
        if centroid > 2000:
            scores[Emotion.ANGRY] += 0.15
        if speech_rate > 8:  # Fast speaking
            scores[Emotion.ANGRY] += 0.1
        
        # WORRIED: moderate energy, high variance (unsteady voice)
        if 0.05 < energy < 0.12:
            scores[Emotion.WORRIED] += 0.2
        if variance > self.high_variance_threshold:
            scores[Emotion.WORRIED] += 0.3
        if 1000 < centroid < 2500:
            scores[Emotion.WORRIED] += 0.1
        
        # CONFUSED: low energy, slow speech, pauses (low speech rate)
        if energy < self.low_energy_threshold * 2:
            scores[Emotion.CONFUSED] += 0.2
        if speech_rate < 3:  # Slow/hesitant speech
            scores[Emotion.CONFUSED] += 0.3
        if variance > self.high_variance_threshold * 0.5:
            scores[Emotion.CONFUSED] += 0.15
        
        # HAPPY: high energy, high ZCR, medium-high centroid
        if energy > self.high_energy_threshold * 0.8:
            scores[Emotion.HAPPY] += 0.2
        if zcr > self.high_zcr_threshold * 0.8:
            scores[Emotion.HAPPY] += 0.15
        if centroid > 1500:
            scores[Emotion.HAPPY] += 0.1
        if speech_rate > 5:
            scores[Emotion.HAPPY] += 0.1
        
        # Pick highest scoring emotion
        best_emotion = max(scores, key=scores.get)
        confidence = min(scores[best_emotion], 1.0)
        
        # If no strong signal, default to neutral
        if confidence < 0.35:
            return Emotion.NEUTRAL, 0.5
        
        return best_emotion, confidence


# Singleton
emotion_detector = EmotionDetector()
