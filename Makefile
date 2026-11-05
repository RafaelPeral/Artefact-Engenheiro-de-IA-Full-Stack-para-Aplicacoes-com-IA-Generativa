# Python owns task execution; Make recipes do not depend on PowerShell/Bash.
.DEFAULT_GOAL := help
ifeq ($(OS),Windows_NT)
PYTHON ?= python
else
PYTHON ?= python3
endif

PROJECT = "$(PYTHON)" -m scripts.project
LIVE ?= 0
.PHONY: help install run test test-live benchmark pip ollama model embeddings models

help:
	@$(PROJECT) help

install:
	@$(PROJECT) install

run:
	@$(PROJECT) run

test:
	@$(PROJECT) test $(if $(filter 1,$(LIVE)),--live,)

test-live:
	@$(PROJECT) test --live

benchmark:
	@$(PROJECT) benchmark

# Optional repair tasks; the normal setup needs only make install.
pip:
	@$(PROJECT) pip

ollama:
	@$(PROJECT) ollama

models:
	@$(PROJECT) models

model:
	@$(PROJECT) model

embeddings:
	@$(PROJECT) embeddings
