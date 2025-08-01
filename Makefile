SHELL := /bin/bash

.PHONY: sync deps test validate run

# 1) Sinhronizacija z GitHub (če obstaja .git mape)
sync:
	if [ -d ".git" ] ; then \
	echo "► git pull origin main" ; \
	git pull origin main ; \
	else \
	echo "► .git ni, preskočujem pull" ; \
	fi

# 2) Namestitev odvisnosti
deps:
	pip install --upgrade pip
	pip install -r requirements.txt
	if [ -f "wsm_program_vnasanje/requirements.txt" ] ; then \
	pip install -r wsm_program_vnasanje/requirements.txt ; \
	fi

# 3) Zagon testov
test:
	pytest -q

# 4) Validacija podatkov
validate:
	python -m wsm validate tests

# 5) Celoten “pipeline” (sync → deps → test)
run: sync deps test
