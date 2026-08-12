.PHONY: help check fix

help:
	@grep -E '^[a-z-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-8s\033[0m %s\n", $$1, $$2}'

check:     ## Проверить символы в документации и коде
	python3 tools/check_punctuation.py

fix:       ## Заменить типографские символы на основные
	python3 tools/check_punctuation.py --fix
