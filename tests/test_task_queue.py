import threading
import pytest
from forgerace.task_queue import TaskQueue

def test_task_queue_thread_safety():
    q = TaskQueue(max_concurrent=1)
    num_tasks = 1000
    num_threads = 10
    
    def worker_push(thread_id):
        for i in range(num_tasks):
            q.push(f"task-{thread_id}-{i}", priority=i)
            
    threads = []
    for i in range(num_threads):
        t = threading.Thread(target=worker_push, args=(i,))
        threads.append(t)
        t.start()
        
    for t in threads:
        t.join()
        
    assert len(q) == num_tasks * num_threads
    
    results = []
    def worker_pop():
        while True:
            task = q.pop()
            if task is None:
                break
            results.append(task)
            
    threads = []
    for i in range(num_threads):
        t = threading.Thread(target=worker_pop)
        threads.append(t)
        t.start()
        
    for t in threads:
        t.join()
        
    assert len(results) == num_tasks * num_threads
    assert len(q) == 0

def test_task_queue_priority():
    q = TaskQueue()
    q.push("low", priority=1)
    q.push("high", priority=10)
    q.push("medium", priority=5)
    
    assert q.pop() == "high"
    assert q.pop() == "medium"
    assert q.pop() == "low"
    assert q.pop() is None

def test_task_queue_stability():
    q = TaskQueue()
    q.push("first", priority=10)
    q.push("second", priority=10)
    
    # heapq is stable if we use a counter, which TaskQueue does.
    assert q.pop() == "first"
    assert q.pop() == "second"
