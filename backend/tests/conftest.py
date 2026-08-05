import sys
import os

# Add backend/ to sys.path so `from app.ai.failure_classifier import ...` works.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
