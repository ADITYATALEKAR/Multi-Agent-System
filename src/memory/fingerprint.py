"""Structural fingerprinting: WL-hash, MinHashLSH, TwoLevelMemoKey.

Phase 3 — Fingerprint computation, indexing, and collision verification.

WL-hash (Weisfeiler-Leman) produces a deterministic hash for a labelled
sub-graph.  MinHashLSH enables approximate nearest-neighbour search.
TwoLevelMemoKey (v3.3 A2) adds a canonical adjacency verification layer
to guard against WL-hash collisions.
"""

from __future__ import annotations

import hashlib
import struct
from collections import defaultdict
from typing import Any
from uuid import UUID

import structlog

log = structlog.get_logger(__name__)

_WL_ITERATIONS: int = 3  # default Weisfeiler-Leman iterations
_LSH_NUM_HASHES: int = 128  # MinHash signature size
_LSH_BANDS: int = 16  # number of LSH bands
_LSH_ROWS_PER_BAND: int = _LSH_NUM_HASHES // _LSH_BANDS  # 8 rows per band


# ---------------------------------------------------------------------------
# WL-hash
# ---------------------------------------------------------------------------


def wl_hash(
    nodes: list[dict[str, Any]],
    edges: list[tuple[int, int]],
    iterations: int = _WL_ITERATIONS,
) -> bytes:
    """Compute a Weisfeiler-Leman graph hash.

    Each node dict must have a ``"label"`` key.  Edges are (src_idx, dst_idx)
    index pairs into the *nodes* list.

    Returns:
        A 32-byte SHA-256 digest encoding the graph structure.
    """
    n = len(nodes)
    if n == 0:
        return hashlib.sha256(b"empty").digest()

    # Build adjacency
    adj: dict[int, list[int]] = defaultdict(list)
    for src, dst in edges:
        adj[src].append(dst)
        adj[dst].append(src)

    # Initial labels
    labels: list[str] = [str(node.get("label", node.get("type", ""))) for node in nodes]

    for _ in range(iterations):
        new_labels: list[str] = []
        for i in range(n):
            neighbour_labels = sorted(labels[j] for j in adj.get(i, []))
            combined = labels[i] + "|" + ",".join(neighbour_labels)
            new_labels.append(hashlib.md5(combined.encode()).hexdigest())
        labels = new_labels

    # Final hash: sort labels for isomorphism invariance, then SHA-256
    canonical = "\n".join(sorted(labels))
    return hashlib.sha256(canonical.encode()).digest()


def canonical_adjacency_hash(
    nodes: list[dict[str, Any]],
    edges: list[tuple[int, int]],
) -> bytes:
    """Compute a canonical adjacency matrix hash for collision verification.

    Sorts nodes by label, builds the adjacency matrix in canonical order,
    and hashes it.  More expensive than WL-hash but collision-resistant.

    Returns:
        A 32-byte SHA-256 digest.
    """
    n = len(nodes)
    if n == 0:
        return hashlib.sha256(b"empty-adj").digest()

    # Sort nodes by label to get canonical ordering
    indexed_labels = [(i, str(nodes[i].get("label", nodes[i].get("type", "")))) for i in range(n)]
    indexed_labels.sort(key=lambda x: x[1])
    # Map from original index -> canonical index
    old_to_new = {old_idx: new_idx for new_idx, (old_idx, _) in enumerate(indexed_labels)}

    # Build adjacency set in canonical space
    edge_set: set[tuple[int, int]] = set()
    for src, dst in edges:
        cs, cd = old_to_new.get(src, src), old_to_new.get(dst, dst)
        edge_set.add((min(cs, cd), max(cs, cd)))

    # Hash: sorted labels + sorted edges
    parts: list[str] = [lbl for _, lbl in indexed_labels]
    parts.append("---")
    for e in sorted(edge_set):
        parts.append(f"{e[0]}-{e[1]}")

    return hashlib.sha256("\n".join(parts).encode()).digest()


# ---------------------------------------------------------------------------
# MinHash / LSH
# ---------------------------------------------------------------------------


class MinHashSignature:
    """Compute a MinHash signature for a set of shingles."""

    def __init__(self, num_hashes: int = _LSH_NUM_HASHES) -> None:
        self._num_hashes = num_hashes
        # Pre-generate hash coefficients (a, b) for h(x) = (a*x + b) mod p
        self._p = (1 << 61) - 1  # Mersenne prime
        import random
        rng = random.Random(42)  # deterministic
        self._coeffs = [
            (rng.randint(1, self._p - 1), rng.randint(0, self._p - 1))
            for _ in range(num_hashes)
        ]

    def compute(self, shingles: set[int]) -> list[int]:
        """Compute the MinHash signature for a set of integer shingles."""
        if not shingles:
            return [self._p] * self._num_hashes

        sig = [self._p] * self._num_hashes
        for shingle in shingles:
            for i, (a, b) in enumerate(self._coeffs):
                h = (a * shingle + b) % self._p
                if h < sig[i]:
                    sig[i] = h
        return sig

    @staticmethod
    def jaccard_estimate(sig_a: list[int], sig_b: list[int]) -> float:
        """Estimate Jaccard similarity from two MinHash signatures."""
        if len(sig_a) != len(sig_b):
            return 0.0
        matches = sum(1 for a, b in zip(sig_a, sig_b) if a == b)
        return matches / len(sig_a)


class MinHashLSH:
    """Locality-Sensitive Hashing index using banded MinHash.

    Partitions each signature into bands.  Two items are candidate
    neighbours if they share at least one identical band.
    """

    def __init__(
        self,
        num_hashes: int = _LSH_NUM_HASHES,
        num_bands: int = _LSH_BANDS,
    ) -> None:
        self._num_hashes = num_hashes
        self._num_bands = num_bands
        self._rows_per_band = num_hashes // num_bands
        self._minhash = MinHashSignature(num_hashes)
        # band_idx -> {band_hash -> set of object_ids}
        self._buckets: list[dict[int, set[UUID]]] = [
            defaultdict(set) for _ in range(num_bands)
        ]
        self._signatures: dict[UUID, list[int]] = {}

    def insert(self, object_id: UUID, shingles: set[int]) -> None:
        """Insert an object with its shingle set."""
        sig = self._minhash.compute(shingles)
        self._signatures[object_id] = sig
        for band_idx in range(self._num_bands):
            start = band_idx * self._rows_per_band
            band = tuple(sig[start: start + self._rows_per_band])
            band_hash = hash(band)
            self._buckets[band_idx][band_hash].add(object_id)

    def query(self, shingles: set[int], threshold: float = 0.5) -> list[tuple[UUID, float]]:
        """Find candidate neighbours above the similarity threshold.

        Returns:
            List of (object_id, estimated_similarity) sorted by similarity descending.
        """
        query_sig = self._minhash.compute(shingles)

        # Gather candidates from any matching band
        candidates: set[UUID] = set()
        for band_idx in range(self._num_bands):
            start = band_idx * self._rows_per_band
            band = tuple(query_sig[start: start + self._rows_per_band])
            band_hash = hash(band)
            candidates.update(self._buckets[band_idx].get(band_hash, set()))

        # Compute exact Jaccard estimate for candidates
        results: list[tuple[UUID, float]] = []
        for obj_id in candidates:
            sim = MinHashSignature.jaccard_estimate(query_sig, self._signatures[obj_id])
            if sim >= threshold:
                results.append((obj_id, sim))

        results.sort(key=lambda x: x[1], reverse=True)
        return results

    @property
    def size(self) -> int:
        return len(self._signatures)


# ---------------------------------------------------------------------------
# TwoLevelMemoKey (v3.3 A2)
# ---------------------------------------------------------------------------


class TwoLevelMemoKey:
    """Two-level memo key: WL-hash for fast lookup + canonical adjacency
    verification on every cache hit.

    Level 1: WL-hash (fast, ~O(|V|·k) with k iterations)
    Level 2: Canonical adjacency hash (slower, collision-resistant)

    Both levels must match for a cache hit to be confirmed.
    """

    def __init__(self, wl_iterations: int = _WL_ITERATIONS) -> None:
        self._wl_iterations = wl_iterations

    def compute_key(
        self,
        nodes: list[dict[str, Any]],
        edges: list[tuple[int, int]],
        environment: str = "",
    ) -> tuple[bytes, bytes]:
        """Compute the two-level key.

        Args:
            nodes: Node dicts with at least a ``"label"`` or ``"type"`` key.
            edges: Edge pairs as (src_idx, dst_idx).
            environment: Environment string included in key (v3.3 C3).

        Returns:
            Tuple of (wl_hash, canonical_hash).
        """
        # Level 1: WL-hash
        wl = wl_hash(nodes, edges, iterations=self._wl_iterations)

        # Mix environment into both hashes (v3.3 C3)
        if environment:
            env_bytes = environment.encode()
            wl = hashlib.sha256(wl + env_bytes).digest()

        # Level 2: canonical adjacency
        canonical = canonical_adjacency_hash(nodes, edges)
        if environment:
            canonical = hashlib.sha256(canonical + env_bytes).digest()

        return wl, canonical

    def verify(
        self,
        cached_canonical: bytes,
        nodes: list[dict[str, Any]],
        edges: list[tuple[int, int]],
        environment: str = "",
    ) -> bool:
        """Verify that a cache hit is genuine (not a WL-hash collision).

        Args:
            cached_canonical: The canonical hash stored with the cached entry.
            nodes: Current sub-graph nodes.
            edges: Current sub-graph edges.
            environment: Environment string.

        Returns:
            True if the canonical hashes match.
        """
        _, current_canonical = self.compute_key(nodes, edges, environment)
        return cached_canonical == current_canonical


# ---------------------------------------------------------------------------
# FingerprintIndex — unified index over WL-hash + LSH
# ---------------------------------------------------------------------------


def _graph_to_shingles(
    nodes: list[dict[str, Any]],
    edges: list[tuple[int, int]],
) -> set[int]:
    """Convert a graph to a set of integer shingles for MinHash.

    Shingles are derived from node labels and edge pairs.
    """
    shingles: set[int] = set()
    for i, node in enumerate(nodes):
        label = str(node.get("label", node.get("type", "")))
        shingles.add(hash(f"node:{label}"))
    for src, dst in edges:
        src_lbl = str(nodes[src].get("label", "")) if src < len(nodes) else ""
        dst_lbl = str(nodes[dst].get("label", "")) if dst < len(nodes) else ""
        shingles.add(hash(f"edge:{src_lbl}->{dst_lbl}"))
    return shingles


class FingerprintIndex:
    """Unified fingerprint index supporting exact and approximate queries.

    Exact queries use a WL-hash → object_id mapping.
    Approximate queries use MinHashLSH for similarity search.
    Collision verification uses TwoLevelMemoKey (v3.3 A2).
    """

    def __init__(self) -> None:
        self._exact: dict[bytes, list[UUID]] = defaultdict(list)
        self._canonical: dict[UUID, bytes] = {}
        self._lsh = MinHashLSH()
        self._two_level = TwoLevelMemoKey()
        # Store graph data for verification
        self._graph_data: dict[UUID, tuple[list[dict], list[tuple[int, int]], str]] = {}

    def insert(
        self,
        object_id: UUID,
        nodes: list[dict[str, Any]],
        edges: list[tuple[int, int]],
        environment: str = "",
    ) -> bytes:
        """Insert a graph fingerprint and return the WL-hash.

        Args:
            object_id: UUID of the object being fingerprinted.
            nodes: Graph nodes with ``"label"``/``"type"`` keys.
            edges: Edge pairs as (src_idx, dst_idx).
            environment: Environment string (v3.3 C3).

        Returns:
            The WL-hash bytes for the inserted graph.
        """
        wl, canonical = self._two_level.compute_key(nodes, edges, environment)
        self._exact[wl].append(object_id)
        self._canonical[object_id] = canonical
        self._graph_data[object_id] = (nodes, edges, environment)

        # LSH index for approximate queries
        shingles = _graph_to_shingles(nodes, edges)
        self._lsh.insert(object_id, shingles)

        log.debug(
            "fingerprint_index.insert",
            object_id=str(object_id),
            wl_hash=wl.hex()[:16],
        )
        return wl

    def query_exact(self, fingerprint: bytes) -> list[UUID]:
        """Return object IDs with an exact WL-hash match."""
        return list(self._exact.get(fingerprint, []))

    def query_approximate(
        self,
        nodes: list[dict[str, Any]],
        edges: list[tuple[int, int]],
        threshold: float = 0.5,
    ) -> list[tuple[UUID, float]]:
        """Find similar graphs using MinHashLSH.

        Returns:
            List of (object_id, similarity) above threshold, sorted descending.
        """
        shingles = _graph_to_shingles(nodes, edges)
        return self._lsh.query(shingles, threshold)

    def verify_collision(
        self,
        fingerprint: bytes,
        candidate_ids: list[UUID],
        nodes: list[dict[str, Any]],
        edges: list[tuple[int, int]],
        environment: str = "",
    ) -> list[UUID]:
        """Verify which candidates are genuine matches (not WL-hash collisions).

        Uses the two-level key (v3.3 A2) to filter out false positives.

        Returns:
            Subset of candidate_ids that pass canonical adjacency verification.
        """
        _, query_canonical = self._two_level.compute_key(nodes, edges, environment)
        verified: list[UUID] = []
        for cid in candidate_ids:
            cached_canonical = self._canonical.get(cid)
            if cached_canonical is not None and cached_canonical == query_canonical:
                verified.append(cid)
        return verified

    @property
    def size(self) -> int:
        return len(self._canonical)
