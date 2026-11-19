PY := $(shell command -v python3 2>/dev/null || command -v python)

test:
	$(PY) -m unittest discover -s tests -v

findings:
	$(PY) plearun_cli.py reports/report_fictional_02.txt records > findings.json
