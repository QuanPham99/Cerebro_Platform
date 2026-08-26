from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "config" / "bank-source.yaml"
DEFAULT_BUNDLE = ROOT / "knowledge" / "bank-workshop"
VENDOR_SRC = ROOT / "vendor" / "open-knowledge-format" / "src"

