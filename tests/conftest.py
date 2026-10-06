# -*- coding: utf-8 -*-
"""把仓库根加进 sys.path，测试直接 import radar。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
