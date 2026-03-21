"""Memory consolidation: compress, extract patterns, and archive episodes.

Phase 3 — Post-episode pipeline:
    1. PatternMatcher — identify recurring patterns across episodes
    2. RuleExtractor — extract semantic rules from clustered episodes
    3. ConfidenceAdjuster — adjust confidence based on outcome feedback
    4. ConsolidationPipeline — orchestrates 1-3, archives old episodes

Compaction target: > 40% storage reduction for families with 15+ episodes.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import structlog

from src.memory.fingerprint import wl_hash
from src.memory.storage import MemoryStore
from src.memory.types import (
    Episode,
    EpisodeOutcome,
    Pattern,
    SemanticRule,
)

log = structlog.get_logger(__name__)

_MIN_CLUSTER_SIZE: int = 3
_ARCHIVE_THRESHOLD: int = 15
_ARCHIVE_KEEP_RECENT: int = 5


# ---------------------------------------------------------------------------
# PatternMatcher
# ---------------------------------------------------------------------------


class PatternMatcher:
    """Identify recurring structural patterns across episodes.

    Groups episodes by their violation fingerprints and extracts
    patterns from groups with >= min_cluster_size members.
    """

    def __init__(self, min_cluster_size: int = _MIN_CLUSTER_SIZE) -> None:
        self._min_cluster_size = min_cluster_size

    def find_patterns(self, episodes: list[Episode]) -> list[Pattern]:
        """Cluster episodes by fingerprint and return new patterns.

        Returns:
            List of Pattern objects for recurring violation structures.
        """
        # Group by fingerprint
        clusters: dict[bytes, list[Episode]] = defaultdict(list)
        for ep in episodes:
            if ep.fingerprint:
                clusters[ep.fingerprint].append(ep)

        patterns: list[Pattern] = []
        for fp, cluster in clusters.items():
            if len(cluster) < self._min_cluster_size:
                continue

            # Extract common law categories
            all_categories: set[str] = set()
            for ep in cluster:
                all_categories.update(ep.law_categories)

            # Collect associated rule IDs from episode metadata
            rule_ids: set[str] = set()
            for ep in cluster:
                for rid in ep.metadata.get("rule_ids", []):
                    rule_ids.add(rid)

            # Compute confidence from success rate
            resolved_count = sum(
                1 for ep in cluster if ep.outcome == EpisodeOutcome.RESOLVED
            )
            confidence = resolved_count / len(cluster) if cluster else 0.0

            pattern = Pattern(
                name=f"Recurring pattern ({len(cluster)} episodes)",
                description=f"Detected across {len(cluster)} episodes with categories {all_categories}",
                signature=fp,
                occurrence_count=len(cluster),
                associated_violations=sorted(rule_ids),
                confidence=min(confidence, 1.0),
                last_seen=max(ep.created_at for ep in cluster),
            )
            patterns.append(pattern)

        log.debug(
            "pattern_matcher.done",
            episodes=len(episodes),
            clusters=len(clusters),
            patterns=len(patterns),
        )
        return patterns


# ---------------------------------------------------------------------------
# RuleExtractor
# ---------------------------------------------------------------------------


class RuleExtractor:
    """Extract semantic rules from clustered episodes.

    A rule is extracted when multiple episodes in the same cluster share
    a common violation pattern and resolution strategy.
    """

    def __init__(self, min_episodes: int = _MIN_CLUSTER_SIZE) -> None:
        self._min_episodes = min_episodes

    def extract(self, episodes: list[Episode]) -> list[SemanticRule]:
        """Extract generalized rules from episode clusters.

        Groups episodes by law_categories + environment, then generates
        rules for groups with sufficient members.
        """
        # Group by (frozen law_categories, environment)
        groups: dict[tuple[frozenset[str], str], list[Episode]] = defaultdict(list)
        for ep in episodes:
            key = (frozenset(ep.law_categories), ep.environment)
            groups[key].append(ep)

        rules: list[SemanticRule] = []
        for (categories, environment), cluster in groups.items():
            if len(cluster) < self._min_episodes:
                continue

            resolved = [ep for ep in cluster if ep.outcome == EpisodeOutcome.RESOLVED]
            if not resolved:
                continue

            confidence = len(resolved) / len(cluster)

            rule = SemanticRule(
                description=(
                    f"When violations in categories {set(categories)} occur in "
                    f"{environment}, the pattern seen in {len(resolved)} resolved "
                    f"episodes applies."
                ),
                condition=f"law_categories={set(categories)} AND environment={environment}",
                conclusion=f"Apply resolution pattern from {len(resolved)} episodes",
                confidence=min(confidence, 1.0),
                supporting_episodes=[ep.episode_id for ep in cluster],
                region=set().union(*(ep.region for ep in cluster)),
                environment=environment,
                law_categories=set(categories),
                match_count=len(cluster),
            )
            rules.append(rule)

        log.debug(
            "rule_extractor.done",
            episodes=len(episodes),
            groups=len(groups),
            rules=len(rules),
        )
        return rules


# ---------------------------------------------------------------------------
# ConfidenceAdjuster
# ---------------------------------------------------------------------------


class ConfidenceAdjuster:
    """Adjust confidence of rules and patterns based on outcome feedback.

    Learning rate controls how much a single observation moves the
    confidence.  Positive outcomes (RESOLVED) increase, negative
    outcomes (FALSE_POSITIVE, ESCALATED) decrease.
    """

    def __init__(self, learning_rate: float = 0.05) -> None:
        self._lr = learning_rate

    def adjust_rule(self, rule: SemanticRule, was_correct: bool) -> float:
        """Adjust and return the new confidence for a semantic rule."""
        if was_correct:
            new_conf = rule.confidence + self._lr * (1.0 - rule.confidence)
        else:
            new_conf = rule.confidence - self._lr * rule.confidence
        new_conf = max(0.01, min(1.0, new_conf))
        rule.confidence = new_conf
        rule.match_count += 1
        rule.last_validated = datetime.now(timezone.utc)
        return new_conf

    def adjust_pattern(self, pattern: Pattern, was_correct: bool) -> float:
        """Adjust and return the new confidence for a pattern."""
        if was_correct:
            new_conf = pattern.confidence + self._lr * (1.0 - pattern.confidence)
        else:
            new_conf = pattern.confidence - self._lr * pattern.confidence
        new_conf = max(0.01, min(1.0, new_conf))
        pattern.confidence = new_conf
        pattern.occurrence_count += 1
        pattern.last_seen = datetime.now(timezone.utc)
        return new_conf


# ---------------------------------------------------------------------------
# ConsolidationPipeline
# ---------------------------------------------------------------------------


class ConsolidationPipeline:
    """Orchestrates post-episode consolidation.

    Pipeline:
        1. Run PatternMatcher to discover new patterns.
        2. Run RuleExtractor to generalize from episodes.
        3. Store new patterns and rules.
        4. Archive old episodes (keep recent N, compress the rest).

    Compaction target: > 40% reduction for families with 15+ episodes.
    """

    def __init__(
        self,
        store: MemoryStore,
        *,
        min_cluster_size: int = _MIN_CLUSTER_SIZE,
        archive_threshold: int = _ARCHIVE_THRESHOLD,
        archive_keep_recent: int = _ARCHIVE_KEEP_RECENT,
    ) -> None:
        self._store = store
        self._pattern_matcher = PatternMatcher(min_cluster_size)
        self._rule_extractor = RuleExtractor(min_cluster_size)
        self._confidence_adjuster = ConfidenceAdjuster()
        self._archive_threshold = archive_threshold
        self._archive_keep_recent = archive_keep_recent

    def consolidate(self, tenant_id: str = "default") -> ConsolidationResult:
        """Run the full consolidation pipeline for a tenant.

        Returns:
            ConsolidationResult with counts of extracted patterns,
            rules, and archived episodes.
        """
        # 1. Gather all episodes
        episodes = self._store.query_episodes(tenant_id, limit=10_000)
        if not episodes:
            return ConsolidationResult()

        # 2. Extract patterns
        new_patterns = self._pattern_matcher.find_patterns(episodes)
        for p in new_patterns:
            p.tenant_id = tenant_id
            self._store.store_pattern(p)

        # 3. Extract rules
        new_rules = self._rule_extractor.extract(episodes)
        for r in new_rules:
            r.tenant_id = tenant_id
            self._store.store_rule(r)

        # 4. Archive old episodes from large families
        archived_count = self._archive_episodes(episodes, tenant_id)

        result = ConsolidationResult(
            patterns_extracted=len(new_patterns),
            rules_extracted=len(new_rules),
            episodes_archived=archived_count,
            episodes_total=len(episodes),
        )

        log.info(
            "consolidation.done",
            tenant_id=tenant_id,
            patterns=result.patterns_extracted,
            rules=result.rules_extracted,
            archived=result.episodes_archived,
            total=result.episodes_total,
        )
        return result

    def _archive_episodes(self, episodes: list[Episode], tenant_id: str) -> int:
        """Archive old episodes from families that exceed the threshold.

        Groups episodes by fingerprint.  For families with more than
        archive_threshold members, keeps only the most recent
        archive_keep_recent and deletes the rest.

        Returns:
            Number of episodes archived (deleted).
        """
        from src.memory.types import MemoryType

        clusters: dict[bytes, list[Episode]] = defaultdict(list)
        for ep in episodes:
            if ep.fingerprint:
                clusters[ep.fingerprint].append(ep)

        archived = 0
        for fp, cluster in clusters.items():
            if len(cluster) < self._archive_threshold:
                continue

            # Sort by created_at descending (most recent first)
            cluster.sort(key=lambda e: e.created_at, reverse=True)

            # Keep recent, archive the rest
            to_archive = cluster[self._archive_keep_recent:]
            for ep in to_archive:
                self._store.delete(MemoryType.EPISODIC, ep.episode_id, tenant_id)
                archived += 1

        return archived


# ---------------------------------------------------------------------------
# ConsolidationResult
# ---------------------------------------------------------------------------


class ConsolidationResult:
    """Result of a consolidation run."""

    def __init__(
        self,
        patterns_extracted: int = 0,
        rules_extracted: int = 0,
        episodes_archived: int = 0,
        episodes_total: int = 0,
    ) -> None:
        self.patterns_extracted = patterns_extracted
        self.rules_extracted = rules_extracted
        self.episodes_archived = episodes_archived
        self.episodes_total = episodes_total

    @property
    def compression_ratio(self) -> float:
        """Fraction of episodes archived (higher = more compression)."""
        if self.episodes_total == 0:
            return 0.0
        return self.episodes_archived / self.episodes_total
