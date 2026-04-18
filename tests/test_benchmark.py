import pytest
from forgerace.benchmark import BenchmarkStore, BenchmarkRecord

def test_benchmark_save_and_get_all(tmp_path):
    json_path = tmp_path / "benchmark.json"
    store = BenchmarkStore(path=json_path)
    
    record1 = BenchmarkRecord("TASK-1", "agent1", 10.5, 0.05, 1, 100)
    record2 = BenchmarkRecord("TASK-2", "agent2", 20.0, 0.10, 2, 200)
    
    store.save(record1)
    store.save(record2)
    
    records = store.get_all()
    assert len(records) == 2
    assert records[0].task_id == "TASK-1"
    assert records[0].agent == "agent1"
    assert records[1].task_id == "TASK-2"
    assert records[1].agent == "agent2"

def test_benchmark_aggregation(tmp_path):
    json_path = tmp_path / "benchmark.json"
    store = BenchmarkStore(path=json_path)
    
    # 10, 20, 30 -> mean 20, median 20
    # 0.1, 0.2, 0.3 -> mean 0.2, median 0.2
    # 1, 2, 3 -> mean 2, median 2
    # 100, 200, 300 -> mean 200, median 200
    records = [
        BenchmarkRecord("T1", "agent1", 10.0, 0.1, 1, 100),
        BenchmarkRecord("T2", "agent1", 20.0, 0.2, 2, 200),
        BenchmarkRecord("T3", "agent1", 30.0, 0.3, 3, 300),
        BenchmarkRecord("T4", "agent2", 100.0, 1.0, 5, 1000),
    ]
    
    for r in records:
        store.save(r)
        
    # Stats for all
    stats = store.aggregate()
    assert stats["duration_sec"]["mean"] == (10+20+30+100) / 4 # 40.0
    assert stats["duration_sec"]["median"] == 25.0 # (20+30)/2
    
    assert stats["total_cost_usd"]["mean"] == pytest.approx((0.1+0.2+0.3+1.0) / 4) # 0.4
    assert stats["total_cost_usd"]["median"] == pytest.approx(0.25) # (0.2+0.3)/2
    
    # Stats for agent1
    stats1 = store.aggregate(agent="agent1")
    assert stats1["duration_sec"]["mean"] == 20.0
    assert stats1["duration_sec"]["median"] == 20.0
    assert stats1["review_rounds"]["mean"] == 2.0
    assert stats1["lines_changed"]["mean"] == 200.0

def test_benchmark_empty_aggregation(tmp_path):
    json_path = tmp_path / "empty.json"
    store = BenchmarkStore(path=json_path)
    assert store.aggregate() == {}

def test_benchmark_invalid_json(tmp_path):
    json_path = tmp_path / "invalid.json"
    json_path.write_text("not a json")
    store = BenchmarkStore(path=json_path)
    
    assert store.get_all() == []
    assert store.aggregate() == {}
