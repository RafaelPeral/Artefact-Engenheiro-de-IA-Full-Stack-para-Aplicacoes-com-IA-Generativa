SHELL := powershell.exe
.SHELLFLAGS := -NoProfile -Command

.PHONY: help install ollama pip model run test

help:
	@Write-Host "$$([char]27)[1;36mAvailable commands:$$([char]27)[0m"
	@Write-Host "$$([char]27)[1;32mmake install$$([char]27)[0m"
	@Write-Host "$$([char]27)[1;33mmake run$$([char]27)[0m"
	@Write-Host "$$([char]27)[1;35mmake ollama$$([char]27)[0m"
	@Write-Host "$$([char]27)[1;36mmake model$$([char]27)[0m"
	@Write-Host "$$([char]27)[1;36mmake test$$([char]27)[0m"

ollama:
	@& ./scripts/setup_ollama.ps1

pip:
	python -m pip install --upgrade pip 
	python -m pip install -r requirements.txt

install:
	@Write-Host "$$([char]27)[1;35mmake pip$$([char]27)[0m"
	$(MAKE) pip
	@Write-Host "$$([char]27)[1;35mmake ollama$$([char]27)[0m"
	$(MAKE) ollama

model:
	python -c "from app.core.tracing import configure_logging; from app.infrastructure.llm.preparation import prepare_model; configure_logging(); prepare_model()"

run:
	python -m uvicorn app.app:app --host 127.0.0.1 --port 8000

test:
	python -m pytest
	
