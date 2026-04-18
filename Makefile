.PHONY: check lint test

check: lint test

lint:
	ruff check .

test:
	python3 -m pytest || [ $$? -eq 5 ]
