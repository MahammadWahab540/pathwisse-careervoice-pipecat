import sys
import os

# Ensure project root is in sys.path for test discovery across all runners
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
