.PHONY: help docs

help:
	@grep -E '^[a-z-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-8s\033[0m %s\n", $$1, $$2}'

docs:      ## Пересобрать PDF из Markdown
	cd docs && for f in 0*.md; do \
		pandoc $$f -o pdf/$${f%.md}.pdf --pdf-engine=xelatex \
		  -V mainfont="DejaVu Sans" -V fontsize=10pt -V geometry:margin=2cm \
		  -V lang=ru --toc --toc-depth=2; done
