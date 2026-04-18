import sys

def run_error_path_tests():
    """
    Simple error‑path tests for the benchmark core.
    These are not unit‑tests (no unittest or pytest usage) – they run
    as a script and exit with a non‑zero status if any check fails.
    """
    # Example: simulate a failure in a benchmark step
    try:
        # Assume core has a function `execute_step` that should raise on error.
        # We import the core module lazily to avoid circular imports.
        from . import core as benchmark_core

        # Create a dummy step that is expected to fail.
        # The real implementation may differ; this is a placeholder.
        def failing_step():
            raise RuntimeError("simulated step failure")

        # Run the step through the core's error handling wrapper if it exists.
        # If the core provides `run_with_error_handling(step)`, use it.
        if hasattr(benchmark_core, "run_with_error_handling"):
            benchmark_core.run_with_error_handling(failing_step)
        else:
            # Direct call – we expect the exception to propagate.
            failing_step()
    except Exception as e:
        # If the exception is the expected simulated one, the test passes.
        expected_msg = "simulated step failure"
        if expected_msg in str(e):
            print("[ERROR PATH TEST] simulated failure correctly propagated")
            return 0
        else:
            print(f"[ERROR PATH TEST] unexpected exception: {e}")
            return 1
    else:
        # No exception – this is a failure of the error‑path test.
        print("[ERROR PATH TEST] expected exception was not raised")
        return 1

if __name__ == "__main__":
    sys.exit(run_error_path_tests())
