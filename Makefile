.PHONY: install test lint demo serve migrate
install:
	python -m pip install -r requirements.lock
	python -m pip install --no-deps -e .
test:
	pytest -q
lint:
	ruff check .
	python scripts/secret_scan.py
demo:
	aegis demo --days 3 --fill all
serve:
	aegis serve
migrate:
	alembic upgrade head
