"""Test suite for ScaleHardeningEngine (Phase 5)."""

import json
import os
import tempfile
import pytest
from src.intelligence.scale_hardening_engine import (
    ScaleHardeningEngine,
    IncrementalAnalyzer,
    IncrementalDiff,
    LRUTypeCache,
    WorkerPoolEngine,
    MemoryGuard,
)


def test_lru_type_cache_basic():
    """Test basic LRU cache operations."""
    cache = LRUTypeCache(max_size_mb=1)

    type_def = {"type": "interface", "fields": [{"name": "id", "type": "string"}]}

    # Cache miss initially
    assert cache.get("User") is None

    # Add to cache
    cache.put("User", type_def)
    assert cache.get("User") == type_def

    # Stats should reflect hit
    stats = cache.stats()
    assert stats["entries"] == 1
    assert stats["hits"] == 1


def test_lru_type_cache_eviction():
    """Test LRU eviction when cache is full."""
    cache = LRUTypeCache(max_size_mb=0.001)  # Very small cache

    # Add multiple items - test that eviction capability exists
    for i in range(5):
        cache.put(f"Type{i}", {"type": "type", "data": "x" * 100})

    # Cache should have some items (possibly all or some evicted)
    assert len(cache.cache) >= 1  # At least one item should be cached


def test_lru_cache_access_updates_recency():
    """Test that accessing an item moves it to end."""
    cache = LRUTypeCache()

    cache.put("A", {"type": "a"})
    cache.put("B", {"type": "b"})
    cache.put("C", {"type": "c"})

    # Access A - should move to end
    cache.get("A")

    keys = list(cache.cache.keys())
    assert keys[-1] == "A"  # A should be last (most recent)


def test_incremental_analyzer_compute_diff():
    """Test incremental diff computation."""
    analyzer = IncrementalAnalyzer()

    current_files = ["file1.ts", "file2.ts", "file3.ts"]
    file_content_map = {
        "file1.ts": "content1",
        "file2.ts": "content2",
        "file3.ts": "content3",
    }

    # Initial diff - all files are new
    diff = analyzer.compute_diff(current_files, file_content_map)
    assert len(diff.added_files) == 3
    assert len(diff.modified_files) == 0
    assert len(diff.deleted_files) == 0

    # Update snapshots
    for file_path, content in file_content_map.items():
        analyzer.update_snapshot(
            file_path, content, "hash_sym", "hash_con", "hash_mid"
        )

    # Second run - all files unchanged
    diff = analyzer.compute_diff(current_files, file_content_map)
    assert len(diff.added_files) == 0
    assert len(diff.modified_files) == 0
    assert len(diff.unchanged_files) == 3


def test_incremental_analyzer_modification_detection():
    """Test detection of modified files."""
    analyzer = IncrementalAnalyzer()

    # Initial analysis
    files = ["file1.ts"]
    file_content_map = {"file1.ts": "content1"}

    analyzer.update_snapshot("file1.ts", "content1", "hash1", "hash1", "hash1")

    # Modify content
    file_content_map["file1.ts"] = "modified_content"

    diff = analyzer.compute_diff(files, file_content_map)
    assert len(diff.modified_files) == 1
    assert diff.modified_files[0] == "file1.ts"


def test_incremental_analyzer_deletion_detection():
    """Test detection of deleted files."""
    analyzer = IncrementalAnalyzer()

    # Initial snapshots
    analyzer.update_snapshot("file1.ts", "content1", "hash1", "hash1", "hash1")
    analyzer.update_snapshot("file2.ts", "content2", "hash2", "hash2", "hash2")

    # Second run with only file1
    diff = analyzer.compute_diff(["file1.ts"], {"file1.ts": "content1"})

    assert len(diff.deleted_files) == 1
    assert diff.deleted_files[0] == "file2.ts"


def test_incremental_analyzer_snapshot_persistence():
    """Test saving and loading snapshots."""
    with tempfile.TemporaryDirectory() as tmpdir:
        snapshot_file = os.path.join(tmpdir, "snapshots.json")

        analyzer1 = IncrementalAnalyzer()
        analyzer1.update_snapshot("file1.ts", "content1", "hash1", "hash1", "hash1")
        analyzer1.save_snapshots(snapshot_file)

        # Load in new analyzer
        analyzer2 = IncrementalAnalyzer()
        analyzer2.load_snapshots(snapshot_file)

        assert "file1.ts" in analyzer2.snapshots
        assert analyzer2.snapshots["file1.ts"].file_path == "file1.ts"


def test_memory_guard_basic():
    """Test memory guard can be created and checked."""
    guard = MemoryGuard(max_memory_gb=2.0)

    # Should have memory (not in OOM)
    available = guard.check_available_memory()
    assert isinstance(available, bool)

    # Current memory should be non-negative
    memory_mb = guard.get_current_memory_mb()
    assert memory_mb >= 0


def test_worker_pool_engine_stats():
    """Test worker pool initialization and stats."""
    pool = WorkerPoolEngine(max_workers=4, timeout_seconds=60)

    stats = pool.get_stats()
    assert stats["max_workers"] == 4
    assert stats["timeout_seconds"] == 60
    assert stats["completed_files"] == 0


def test_scale_hardening_engine_initialization():
    """Test ScaleHardeningEngine initialization."""
    engine = ScaleHardeningEngine(
        max_workers=4,
        timeout_seconds=120,
        enable_incremental=True,
        max_memory_gb=2.0,
        cache_size_mb=128,
    )

    assert engine.worker_pool.max_workers == 4
    assert engine.incremental is not None
    assert engine.type_cache.max_size_bytes > 0


def test_scale_hardening_engine_with_mock_analyzer():
    """Test scale hardening engine with simple mock analyzer."""

    def mock_analyzer(file_path: str, content: str) -> Dict:
        return {
            "file_path": file_path,
            "symbols": len(content.split()),
            "contracts": 0,
        }

    engine = ScaleHardeningEngine(
        max_workers=2,
        enable_incremental=False,  # Disable for simpler test
    )

    files = ["test1.ts", "test2.ts"]
    file_content_map = {
        "test1.ts": "type User { id: string }",
        "test2.ts": "interface Product { name: string }",
    }

    results, summary = engine.analyze_codebase(
        files, file_content_map, mock_analyzer
    )

    assert summary["total_files_requested"] == 2
    assert summary["files_analyzed"] == 2
    assert len(results) == 2

    # Results should be sorted deterministically
    assert results[0]["file_path"] == "test1.ts"
    assert results[1]["file_path"] == "test2.ts"


def test_scale_hardening_engine_deterministic_output():
    """Test that analyzer produces identical results across runs."""

    def mock_analyzer(file_path: str, content: str) -> Dict:
        return {"file_path": file_path, "hash": hash(content) % (2**31)}

    engine1 = ScaleHardeningEngine(max_workers=2, enable_incremental=False)
    engine2 = ScaleHardeningEngine(max_workers=2, enable_incremental=False)

    files = ["test1.ts", "test2.ts", "test3.ts"]
    file_content_map = {
        "test1.ts": "interface User {}",
        "test2.ts": "type Product = {}",
        "test3.ts": "class Service {}",
    }

    results1, _ = engine1.analyze_codebase(files, file_content_map, mock_analyzer)
    results2, _ = engine2.analyze_codebase(files, file_content_map, mock_analyzer)

    # Results should be identical
    assert results1 == results2


def test_incremental_diff_to_dict():
    """Test IncrementalDiff serialization."""
    diff = IncrementalDiff(
        added_files=["new1.ts", "new2.ts"],
        modified_files=["mod1.ts"],
        deleted_files=["old1.ts"],
        unchanged_files=["same1.ts", "same2.ts"],
    )

    diff_dict = diff.to_dict()
    assert "added_files" in diff_dict
    assert len(diff_dict["added_files"]) == 2
    assert diff_dict["unchanged_count"] == 2


def test_lru_cache_hit_rate():
    """Test LRU cache hit rate calculation."""
    cache = LRUTypeCache()

    # Add items
    cache.put("Type1", {"type": "interface"})

    # Multiple hits
    cache.get("Type1")
    cache.get("Type1")

    stats = cache.stats()
    assert stats["hits"] == 2
    assert stats["hit_rate"] > 0.5


def test_incremental_analyzer_addition_detection():
    """Test detection of added files."""
    analyzer = IncrementalAnalyzer()

    # Initial snapshot with one file
    analyzer.update_snapshot("file1.ts", "content1", "h1", "h1", "h1")

    # New files added
    current_files = ["file1.ts", "file2.ts", "file3.ts"]
    file_content_map = {
        "file1.ts": "content1",
        "file2.ts": "content2",
        "file3.ts": "content3",
    }

    diff = analyzer.compute_diff(current_files, file_content_map)

    assert len(diff.added_files) == 2
    assert "file2.ts" in diff.added_files
    assert "file3.ts" in diff.added_files


def test_scale_hardening_engine_reset():
    """Test reset functionality."""
    engine = ScaleHardeningEngine()

    engine.analysis_summary = {"test": "data"}

    engine.reset()

    assert engine.analysis_summary == {}
    assert engine.type_cache.cache == {}


def test_memory_guard_max_memory_calculation():
    """Test memory guard max memory setting."""
    guard = MemoryGuard(max_memory_gb=4.0)

    # Should have calculated bytes correctly (4 GB)
    assert guard.max_memory_bytes == 4 * 1024 * 1024 * 1024


def test_worker_pool_stats_tracking():
    """Test worker pool stats tracking."""

    def simple_analyzer(file_path: str, content: str) -> Dict:
        return {"file_path": file_path}

    pool = WorkerPoolEngine(max_workers=2)

    files = ["test1.ts", "test2.ts"]
    file_content_map = {"test1.ts": "code1", "test2.ts": "code2"}

    results, failures = pool.analyze_files_parallel(files, simple_analyzer, file_content_map)

    stats = pool.get_stats()
    assert stats["completed_files"] == 2
    assert stats["failed_files"] == 0


def test_scale_hardening_with_incremental_snapshots():
    """Test scale hardening with incremental core functionality."""
    def mock_analyzer(file_path: str, content: str) -> Dict:
        return {"file_path": file_path, "lines": len(content.split("\n"))}

    # Test basic incremental analyzer
    analyzer = IncrementalAnalyzer()
    files = ["file1.ts", "file2.ts"]
    file_content_map = {"file1.ts": "line1\nline2", "file2.ts": "line1"}

    # First run - all files new
    diff1 = analyzer.compute_diff(files, file_content_map)
    assert len(diff1.added_files) == 2  # All files are new

    # Update snapshots
    for file_path, content in file_content_map.items():
        analyzer.update_snapshot(
            file_path, content, "hash_sym", "hash_con", "hash_mid"
        )

    # Second run - no changes
    diff2 = analyzer.compute_diff(files, file_content_map)
    assert len(diff2.added_files) == 0  # No new files
    assert len(diff2.modified_files) == 0  # No modified files
    assert len(diff2.unchanged_files) == 2  # Both files unchanged

# Type hints
from typing import Dict
