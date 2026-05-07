from forgerace.utils import parse_pytest_output, format_duration

def test_parse_pytest_output_empty():
    assert parse_pytest_output("") == []
    assert parse_pytest_output(None) == []

def test_format_duration_zero():
    assert format_duration(0) == "0s"

def test_format_duration_seconds():
    assert format_duration(5) == "5s"
    assert format_duration(59) == "59s"

def test_format_duration_minutes():
    assert format_duration(60) == "1m 0s"
    assert format_duration(119) == "1m 59s"
    assert format_duration(120) == "2m 0s"

def test_format_duration_hours():
    assert format_duration(3600) == "1h 0s"
    assert format_duration(3660) == "1h 1m 0s"
    assert format_duration(3661) == "1h 1m 1s"
    assert format_duration(7200) == "2h 0s"

def test_format_duration_combined():
    assert format_duration(3665) == "1h 1m 5s"
    assert format_duration(7265) == "2h 1m 5s"

def test_format_duration_negative():
    try:
        format_duration(-1)
        assert False, "Expected ValueError"
    except ValueError:
        pass

def test_parse_pytest_output_standard():
    output = """
=========================== short test summary info ============================
FAILED tests/temp_test_fail.py::test_fail - assert False
FAILED tests/temp_test_fail.py::TestClass::test_method_fail - assert 1 == 2
========================= 2 failed, 1 passed in 0.48s ==========================
"""
    expected = [
        "tests/temp_test_fail.py::test_fail",
        "tests/temp_test_fail.py::TestClass::test_method_fail"
    ]
    assert parse_pytest_output(output) == expected

def test_parse_pytest_output_verbose():
    output = """
collected 3 items                                                              

tests/temp_test_fail.py::test_pass PASSED                                [ 33%]
tests/temp_test_fail.py::test_fail FAILED                                [ 66%]
tests/temp_test_fail.py::TestClass::test_method_fail FAILED              [100%]

=================================== FAILURES ===================================
...
"""
    expected = [
        "tests/temp_test_fail.py::test_fail",
        "tests/temp_test_fail.py::TestClass::test_method_fail"
    ]
    assert parse_pytest_output(output) == expected

def test_parse_pytest_output_errors():
    output = """
=========================== short test summary info ============================
ERROR tests/temp_test_syntax.py
ERROR tests/temp_test_fix.py::test_fix_fail - assert 1 == 2
"""
    expected = [
        "tests/temp_test_syntax.py",
        "tests/temp_test_fix.py::test_fix_fail"
    ]
    assert parse_pytest_output(output) == expected

def test_parse_pytest_output_combined():
    output = """
tests/test_a.py::test_1 FAILED
FAILED tests/test_b.py::test_2 - Error
ERROR tests/test_c.py::test_3
"""
    # Should find all and deduplicate if necessary
    results = parse_pytest_output(output)
    assert "tests/test_a.py::test_1" in results
    assert "tests/test_b.py::test_2" in results
    assert "tests/test_c.py::test_3" in results
    assert len(results) == 3

def test_parse_pytest_output_no_dash():
    output = "FAILED tests/test_foo.py::test_bar"
    assert parse_pytest_output(output) == ["tests/test_foo.py::test_bar"]

def test_parse_pytest_output_with_init():
    output = "FAILED tests/__init__.py::test_in_init - Error"
    assert parse_pytest_output(output) == ["tests/__init__.py::test_in_init"]

def test_parse_pytest_output_xdist():
    output = """
=========================== short test summary info ============================
FAILED [gw0] tests/test_a.py::test_1 - AssertionError
ERROR [gw1] tests/test_b.py - ImportError
"""
    expected = ["tests/test_a.py::test_1", "tests/test_b.py"]
    assert parse_pytest_output(output) == expected

def test_parse_pytest_output_colon():
    output = "FAILED: tests/test_colon.py::test_1"
    assert parse_pytest_output(output) == ["tests/test_colon.py::test_1"]

def test_parse_pytest_output_long():
    # Генерируем длинный вывод с кучей мусора и несколькими ошибками
    noise = "Some random application log line with FAILED and ERROR words but not at start\n" * 1000
    failures = "FAILED tests/test_long_1.py::test_1 - Error\n"
    failures += "ERROR tests/test_long_2.py - Error\n"
    output = noise + "=========================== short test summary info ============================\n" + failures + noise
    
    import time
    start = time.time()
    results = parse_pytest_output(output)
    duration = time.time() - start
    
    assert results == ["tests/test_long_1.py::test_1", "tests/test_long_2.py"]
    assert duration < 1.0  # Должно быть быстро
