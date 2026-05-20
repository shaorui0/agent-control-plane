.PHONY: install test demo serve lint verify clean

install:
	pip install -e .[dev]

test:
	pytest -xvs

demo:
	./demo/run_demo.sh

serve:
	uvicorn acp.server:app --reload --port 8080

lint:
	ruff check src tests
	pyright src

verify:
	acp verify

clean:
	rm -rf build dist *.egg-info .pytest_cache .ruff_cache .pyright_cache
	find . -type d -name __pycache__ -exec rm -rf {} +
	rm -f *.db *.db-shm *.db-wal
