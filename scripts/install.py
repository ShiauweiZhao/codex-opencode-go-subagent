#!/usr/bin/env python3
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from codex_opencode_go_bridge.installer import main


raise SystemExit(main())
