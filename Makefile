.PHONY: setup validate price clean

setup:
	python3 -m venv .venv && . .venv/bin/activate && python -m pip install -U pip && pip install -r requirements.txt

validate:
	. .venv/bin/activate && python -m quote_engine.cli validate MV_reports

price:
	. .venv/bin/activate && python -m quote_engine.cli price MV_reports --configs configs

web:
	. .venv/bin/activate && python -m quote_engine.web

clean:
	rm -rf .venv MV_reports/out **/__pycache__
