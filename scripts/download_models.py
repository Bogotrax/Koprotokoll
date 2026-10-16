"""Alle Modelle vorab herunterladen, damit der Koprotokoll danach komplett offline läuft.

    python scripts/download_models.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from koprotokoll.config import Settings  # noqa: E402
from koprotokoll.speech import ensure_diarization_models  # noqa: E402


def main() -> None:
    import onnx_asr

    s = Settings()
    s.ensure_dirs()
    print(f"Parakeet: {s.asr_model} ({s.asr_quantization or 'fp32'}) …")
    onnx_asr.load_model(s.asr_model, s.asr_model_path, quantization=s.asr_quantization)
    onnx_asr.load_vad("silero")
    print("Sprechererkennung …")
    seg, emb = ensure_diarization_models(s.models_dir)
    print(f"Fertig.\n  {seg}\n  {emb}")


if __name__ == "__main__":
    main()
