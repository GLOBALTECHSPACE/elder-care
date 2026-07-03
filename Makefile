.PHONY: install playground run test

install:
	uv sync --python python

playground:
	uv run adk web elder_care --host 127.0.0.1 --port 18081 --reload_agents

run:
	uv run uvicorn elder_care.fast_api_app:app --host 0.0.0.0 --port 8000

test:
	uv run pytest tests/unit
