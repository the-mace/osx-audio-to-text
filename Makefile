.PHONY: help install install-dev install-finder test lint clean check

BLUE := \033[36m
RESET := \033[0m
PYTHON ?= /usr/local/bin/python3

help:  ## Show this help message
	@echo "$(BLUE)osx-audio-to-text — available commands:$(RESET)"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  $(BLUE)%-20s$(RESET) %s\n", $$1, $$2}'

install:  ## Install CLI + Finder Quick Action
	@echo "$(BLUE)Installing osx-audio-to-text...$(RESET)"
	@if [ -x /usr/local/bin/python3 ]; then \
		/usr/local/bin/python3 -m pip install -e .; \
	else \
		python3 -m pip install -e .; \
	fi
	@echo "$(BLUE)Creating CLI symlinks...$(RESET)"
	@for cmd in audio-to-text meeting-summary; do \
		INSTALLED_PATH=$$(find /Library/Frameworks/Python.framework/Versions/*/bin/$$cmd 2>/dev/null | head -1); \
		if [ -z "$$INSTALLED_PATH" ]; then \
			INSTALLED_PATH=$$(command -v $$cmd || true); \
		fi; \
		if [ -n "$$INSTALLED_PATH" ]; then \
			mkdir -p "$$HOME/.local/bin"; \
			ln -sf "$$INSTALLED_PATH" "$$HOME/.local/bin/$$cmd"; \
			echo "  ✓ $$HOME/.local/bin/$$cmd -> $$INSTALLED_PATH"; \
			if ln -sf "$$INSTALLED_PATH" /usr/local/bin/$$cmd 2>/dev/null; then \
				echo "  ✓ /usr/local/bin/$$cmd -> $$INSTALLED_PATH"; \
			elif sudo -n ln -sf "$$INSTALLED_PATH" /usr/local/bin/$$cmd 2>/dev/null; then \
				echo "  ✓ /usr/local/bin/$$cmd -> $$INSTALLED_PATH"; \
			fi; \
		else \
			echo "  Warning: $$cmd command not found after pip install"; \
		fi; \
	done
	@$(MAKE) install-finder
	@echo "$(BLUE)✓ Installed$(RESET)"
	@echo "Usage: meeting-summary ~/Desktop/recording.m4a"
	@echo "       audio-to-text ~/Desktop/clip.mp4   # sidecar .txt only"
	@echo "Finder: right-click audio/MP4 → Quick Actions → Convert to Text"

install-finder:  ## Sign+import Convert to Text Shortcuts Quick Action
	@bash "$(CURDIR)/scripts/install-finder-action.sh"

install-dev:  ## Install with pytest/flake8
	$(PYTHON) -m pip install -e ".[dev]"

test:  ## Run tests
	$(PYTHON) -m pytest -v

lint:  ## Lint with flake8
	$(PYTHON) -m flake8

clean:  ## Remove caches
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .coverage htmlcov/ dist/ build/ *.egg-info

check: lint test  ## Lint + test
