.PHONY: help test benchmark docker-test docker-benchmark docker-build clean

help:
	@echo "Available commands:"
	@echo "  make docker-build      Build the Docker container image"
	@echo "  make docker-test       Run all automated tests inside Docker"
	@echo "  make docker-benchmark  Run the 50-iteration verification benchmark inside Docker"
	@echo "  make test              Run pytest locally (requires python 3.11+ environment)"
	@echo "  make benchmark         Run verification benchmark locally"

docker-build:
	docker compose build

docker-test:
	docker compose run --rm test

docker-benchmark:
	docker compose run --rm benchmark

test:
	pytest -v

benchmark:
	python scripts/run_benchmark.py

clean:
	rm -rf __pycache__ .pytest_cache *.egg-info build dist
	find . -type d -name "__pycache__" -exec rm -rf {} +
