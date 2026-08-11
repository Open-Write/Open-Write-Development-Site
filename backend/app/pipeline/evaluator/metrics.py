"""
metrics.py — Step 2: Deterministic DraftMetrics pre-pass.

Pure functions, no LLM calls. These run on every Class II/III attempt and
are handed to the Evaluator as pre-computed evidence. The Evaluator must
never be asked to count things.

Every metric is unit-tested against hand-labeled fixtures.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional


# ── Data types ───────────────────────────────────────────────────────────────

@dataclass
class BeatCoverage:
    """Coverage status for a single beat from the brief."""
    beat_id: str
    key_text: str           # the beat's text (from the brief)
    status: str             # "present" | "partial" | "absent"
    confidence: str         # "high" (keyword match) | "low" (overlap heuristic)
    matched_spans: list[str] = field(default_factory=list)


@dataclass
class ParagraphClassification:
    """Classification of a paragraph as dramatized scene or summary."""
    index: int
    is_summary: bool
    signals: list[str] = field(default_factory=list)  # e.g. ["time_skip", "no_dialogue"]


@dataclass
class DraftMetrics:
    """Computed metrics for a single generation attempt.

    These are handed to the Evaluator as evidence. Every number here is
    deterministic — computed from the text, not estimated by an LLM.
    """
    word_count: int
    target: int
    delta_pct: float              # (word_count - target) / target * 100
    delta_trend: list[float]      # delta_pct across all attempts on this node
    beat_coverage: list[BeatCoverage]
    beat_density: float           # beats covered per 1,000 words
    dialogue_ratio: float         # proportion of words inside quoted speech
    scene_summary_ratio: float    # ratio of paragraphs classified as summary
    summary_paragraphs: list[int] # indices of paragraphs classified as summary
    distinct_locations: int
    time_jump_count: int
    repetition_score: float       # max 8-gram repetition ratio
    sensory_density: float        # concrete noun + sensory verb rate per 100 words
    beat_coverage_method: str     # "keyword_overlap" | "structured_key"
    total_paragraphs: int


# ── Sensory word lists ───────────────────────────────────────────────────────

_CONCRETE_NOUNS = {
    "table", "chair", "wall", "floor", "ceiling", "door", "window", "glass",
    "stone", "wood", "metal", "paper", "cloth", "leather", "rope", "knife",
    "sword", "gun", "blood", "bone", "flesh", "skin", "hair", "eye", "hand",
    "finger", "arm", "leg", "foot", "face", "head", "body", "chest", "breath",
    "voice", "sound", "noise", "silence", "light", "shadow", "dark", "sun",
    "moon", "star", "rain", "snow", "wind", "fire", "smoke", "ash", "water",
    "river", "sea", "mountain", "road", "path", "tree", "leaf", "flower",
    "grass", "earth", "mud", "dust", "sand", "rock", "iron", "gold", "silver",
    "copper", "glass", "wood", "rope", "chain", "lock", "key", "clock", "bell",
}

_SENSORY_VERBS = {
    "saw", "see", "seen", "look", "looked", "gaze", "gazed", "stare", "stared",
    "watch", "watched", "glance", "glanced", "hear", "heard", "listen", "listened",
    "feel", "felt", "touch", "touched", "taste", "tasted", "smell", "smelled",
    "sniff", "sniffed", "whisper", "whispered", "murmur", "murmured", "shout",
    "shouted", "scream", "screamed", "cry", "cried", "gasp", "gasped", "sigh",
    "sighed", "grunt", "grunted", "wince", "winced", "shiver", "shivered",
    "tremble", "trembled", "ache", "ached", "burn", "burned", "sting", "stung",
    "throb", "throbbed", "pulse", "pulsed", "grip", "gripped", "clutch", "clutched",
}

_TIME_SKIP_CONNECTIVES = {
    "later", "afterward", "afterwards", "eventually", "subsequently",
    "the next morning", "the next day", "the following day", "the following week",
    "over the following", "in the days that followed", "as the weeks passed",
    "by the time", "when next", "some time later", "hours later", "days later",
    "weeks later", "months later", "years later", "that evening", "that night",
    "the morning after", "the afternoon", "meanwhile",
}

_LOCATION_MARKERS = {
    "int.", "ext.", "interior", "exterior", "inside", "outside",
    "room", "hall", "hallway", "corridor", "kitchen", "bedroom", "study",
    "office", "street", "road", "path", "garden", "yard", "forest", "woods",
    "field", "hill", "mountain", "river", "bridge", "church", "temple",
    "house", "building", "tower", "castle", "palace", "cave", "tent",
}


# ── Core metrics ─────────────────────────────────────────────────────────────

def compute_metrics(
    text: str,
    target: int,
    beats: list[str],
    delta_trend: Optional[list[float]] = None,
) -> DraftMetrics:
    """Compute all metrics for a generation attempt.

    Args:
        text: The clean prose text (after strip_artifacts).
        target: The per-chapter word target.
        beats: List of beat descriptions from the chapter plan/brief.
        delta_trend: delta_pct values from prior attempts on this node.

    Returns:
        DraftMetrics with all fields populated.
    """
    words = text.split()
    word_count = len(words)
    delta_pct = ((word_count - target) / target * 100) if target > 0 else 0.0

    # Beat coverage
    coverage, method = _compute_beat_coverage(text, beats)
    covered = sum(1 for b in coverage if b.status in ("present", "partial"))
    beat_density = (covered / max(word_count, 1)) * 1000

    # Dialogue ratio
    dialogue_ratio = _compute_dialogue_ratio(text)

    # Scene vs summary classification
    paragraphs = _split_paragraphs(text)
    classifications = _classify_paragraphs(paragraphs)
    summary_indices = [c.index for c in classifications if c.is_summary]
    summary_ratio = len(summary_indices) / max(len(classifications), 1)

    # Location and time jump detection
    distinct_locations = _count_distinct_locations(text)
    time_jumps = _count_time_jumps(text)

    # Repetition score
    rep_score = _repetition_score(words, n=8)

    # Sensory density
    sensory = _count_sensory_words(words)
    sensory_density = (sensory / max(word_count, 1)) * 100

    return DraftMetrics(
        word_count=word_count,
        target=target,
        delta_pct=round(delta_pct, 1),
        delta_trend=(delta_trend or []) + [round(delta_pct, 1)],
        beat_coverage=coverage,
        beat_density=round(beat_density, 2),
        dialogue_ratio=round(dialogue_ratio, 3),
        scene_summary_ratio=round(summary_ratio, 3),
        summary_paragraphs=summary_indices,
        distinct_locations=distinct_locations,
        time_jump_count=time_jumps,
        repetition_score=round(rep_score, 3),
        sensory_density=round(sensory_density, 2),
        beat_coverage_method=method,
        total_paragraphs=len(classifications),
    )


def improvement_delta(
    metrics_n: DraftMetrics,
    metrics_n1: DraftMetrics,
    deficient_metric: str = "word_count",
) -> float:
    """Return the change on the deficient metric between two attempts.

    Positive means improvement. Used by the loop guard in Step 6.
    """
    val_n = getattr(metrics_n, deficient_metric, 0)
    val_n1 = getattr(metrics_n1, deficient_metric, 0)

    if deficient_metric == "word_count":
        return float(val_n1 - val_n)
    elif deficient_metric in ("scene_summary_ratio", "repetition_score"):
        # Lower is better for these metrics
        return float(val_n - val_n1)
    elif deficient_metric in ("dialogue_ratio", "sensory_density", "beat_density"):
        # Higher is better
        return float(val_n1 - val_n)
    else:
        return float(val_n1 - val_n)


# ── Beat coverage ────────────────────────────────────────────────────────────

def _compute_beat_coverage(
    text: str, beats: list[str]
) -> tuple[list[BeatCoverage], str]:
    """For each beat in the brief, determine if it's present in the text.

    Returns (coverage_list, method_used).
    method_used is "keyword_overlap" for free-text beats. The Evaluator can
    discount low-confidence coverage accordingly.
    """
    text_lower = text.lower()
    coverage = []

    for i, beat in enumerate(beats):
        beat_id = f"beat_{i + 1}"
        beat_lower = beat.lower()

        # Extract significant keywords (3+ chars, not stopwords)
        stopwords = {"the", "and", "for", "that", "this", "with", "from", "about",
                      "what", "when", "where", "which", "their", "there", "then",
                      "than", "them", "they", "have", "has", "had", "was", "were",
                      "are", "not", "but", "can", "may", "will", "would", "could",
                      "should", "into", "over", "under", "between", "through"}
        keywords = [w for w in re.findall(r'\b\w{3,}\b', beat_lower) if w not in stopwords]

        if not keywords:
            coverage.append(BeatCoverage(
                beat_id=beat_id, key_text=beat[:80],
                status="absent", confidence="low",
            ))
            continue

        matched = [kw for kw in keywords if kw in text_lower]
        match_ratio = len(matched) / len(keywords)

        if match_ratio >= 0.6:
            status = "present"
        elif match_ratio >= 0.3:
            status = "partial"
        else:
            status = "absent"

        coverage.append(BeatCoverage(
            beat_id=beat_id, key_text=beat[:80],
            status=status, confidence="low",
            matched_spans=matched[:5],
        ))

    return coverage, "keyword_overlap"


# ── Dialogue ratio ───────────────────────────────────────────────────────────

_QUOTE_PATTERN = re.compile(r'\u201c[^\u201d]{2,}\u201d|\u201c[^\u201d]{2,}\u201d|"[^"]{2,}"')

def _compute_dialogue_ratio(text: str) -> float:
    """Proportion of words inside quoted speech."""
    words = text.split()
    if not words:
        return 0.0

    dialogue_words = 0
    for match in _QUOTE_PATTERN.finditer(text):
        dialogue_words += len(match.group().split())

    return dialogue_words / len(words)


# ── Scene vs summary classification ──────────────────────────────────────────

def _split_paragraphs(text: str) -> list[str]:
    """Split text into paragraphs (double-newline separated)."""
    paras = re.split(r'\n\s*\n', text.strip())
    return [p.strip() for p in paras if p.strip()]


def _classify_paragraphs(paragraphs: list[str]) -> list[ParagraphClassification]:
    """Classify each paragraph as dramatized scene or summary narration.

    Heuristic signals for summary:
    - Time-skip connectives ("Later", "The next morning", "Over the following weeks")
    - Absence of dialogue
    - Past-perfect density ("had been", "had gone", "had thought")
    - Absence of concrete sensory nouns
    """
    classifications = []
    for i, para in enumerate(paragraphs):
        signals = []
        para_lower = para.lower()
        words = para.split()

        # Time-skip connectives
        for conn in _TIME_SKIP_CONNECTIVES:
            if conn in para_lower:
                signals.append("time_skip")
                break

        # Absence of dialogue
        if not _QUOTE_PATTERN.search(para):
            signals.append("no_dialogue")

        # Past-perfect density
        past_perfect = len(re.findall(r'\bhad\s+\w+', para_lower))
        if len(words) > 0 and past_perfect / len(words) > 0.03:
            signals.append("past_perfect_dense")

        # Absence of concrete sensory nouns
        sensory_count = sum(1 for w in words if w.lower() in _CONCRETE_NOUNS)
        if sensory_count == 0 and len(words) > 20:
            signals.append("no_sensory")

        is_summary = len(signals) >= 2
        classifications.append(ParagraphClassification(
            index=i, is_summary=is_summary, signals=signals,
        ))

    return classifications


# ── Location and time jump detection ─────────────────────────────────────────

def _count_distinct_locations(text: str) -> int:
    """Count distinct location markers in the text."""
    text_lower = text.lower()
    found = set()
    for marker in _LOCATION_MARKERS:
        if marker in text_lower:
            found.add(marker)
    return len(found)


def _count_time_jumps(text: str) -> int:
    """Count time-skip connectives in the text."""
    text_lower = text.lower()
    count = 0
    for conn in _TIME_SKIP_CONNECTIVES:
        count += text_lower.count(conn)
    return count


# ── Repetition score ─────────────────────────────────────────────────────────

def _repetition_score(words: list[str], n: int = 8) -> float:
    """Max n-gram repetition ratio. A score above 0.4 indicates a loop."""
    if len(words) < n:
        return 0.0

    lower = [w.lower() for w in words]
    ngrams: Counter[tuple[str, ...]] = Counter()
    for i in range(len(lower) - n + 1):
        gram = tuple(lower[i:i + n])
        ngrams[gram] += 1

    if not ngrams:
        return 0.0

    total = sum(ngrams.values())
    max_count = max(ngrams.values())
    return max_count / total


# ── Sensory density ──────────────────────────────────────────────────────────

def _count_sensory_words(words: list[str]) -> int:
    """Count concrete nouns + sensory verbs in the word list."""
    count = 0
    for w in words:
        wl = w.lower().strip('.,;:!?()"\'-')
        if wl in _CONCRETE_NOUNS or wl in _SENSORY_VERBS:
            count += 1
    return count
