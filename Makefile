REMOTE ?= origin

EDA_COLLECTION_ROOT := .

# Get all .py files in the EDA_COLLECTION_ROOT directory
PY_FILES := $(shell find $(EDA_COLLECTION_ROOT) -name *.py)

VERSION := $(shell grep '^version: ' "$(EDA_COLLECTION_ROOT)/galaxy.yml" | cut -d' ' -f2)

PY_VERSION := $(shell cat .python-version)

EDA_COLLECTION = junipernetworks-eda-$(VERSION).tar.gz

KIND_VERSION := v0.25.0
KUBERNETES_VERSION := v1.29.10

.PHONY: setup build release-build install clean clean-pipenv pipenv test clean-test-bin

# Detect platform
MACHINE := $(shell uname -m)
ifeq ($(MACHINE),x86_64)
	arch := amd64
else ifeq ($(MACHINE),aarch64)
	arch := arm64
else
	arch := $(MACHINE)
endif

# OS-specific settings
OS := $(shell uname -s)
ifeq ($(OS),Darwin)
PYENV_INSTALL_PREFIX := PYTHON_CONFIGURE_OPTS=--enable-framework
os := darwin
else
os=linux
endif

# Determine the URL's for programs to download
kind_url := https://kind.sigs.k8s.io/dl/$(KIND_VERSION)/kind-$(os)-$(arch)
kubectl_url := https://dl.k8s.io/release/$(KUBERNETES_VERSION)/bin/$(os)/$(arch)/kubectl

# By default use .venv in the current directory
export PIPENV_VENV_IN_PROJECT=1

setup: clean-pipenv
	pyenv uninstall --force $(PY_VERSION)
	rm -rf $(HOME)/.pyenv/versions/$(PY_VERSION)
	$(PYENV_INSTALL_PREFIX) pyenv install --force $(PY_VERSION)
	$(MAKE) pipenv

define install_collection_if_missing
	pipenv run ansible-doc $(1) &>/dev/null || pipenv run ansible-galaxy collection install --ignore-certs --force $(1)
endef

pipenv:
	pipenv check 2>/dev/null || \
		(pip install pipenv pre-commit && \
		 pre-commit install && \
		 pipenv install --dev)

clean-pipenv:
	pipenv --rm 2>/dev/null || true
	PIPENV_VENV_IN_PROJECT= pipenv --rm 2>/dev/null || true
	rm -rf .venv

tag:
	git tag -a $(VERSION) -m "Release $(VERSION)"
	git push $(REMOTE) $(VERSION)
	git push --tags

image: build
	mkdir -p build/collections
	rm -f build/collections/junipernetworks-apstra.tar.gz
	cp "$(EDA_COLLECTION)" build/collections/junipernetworks-eda.tar.gz
	TAG=$(VERSION) pipenv run build/build_image.sh

test-bin: Makefile test-bin/kubectl test-bin/kind

test-bin/kind:
	mkdir -p test-bin
	curl -Lso "$@" $(kind_url)
	chmod +x "$@"

test-bin/kubectl:
	mkdir -p test-bin
	curl -Lso "$@" $(kubectl_url)
	chmod +x "$@"

clean-test-bin:
	rm -rf test-bin

$(EDA_COLLECTION_ROOT)/requirements.txt: pipenv
	pipenv requirements --from-pipfile > "$@"

release-build: pipenv $(EDA_COLLECTION_ROOT)/requirements.txt
	rm -f $(EDA_COLLECTION_ROOT)/.eda-collection
	make build

build: $(EDA_COLLECTION_ROOT)/.eda-collection

test: pipenv
	pipenv run pytest

$(EDA_COLLECTION_ROOT)/.eda-collection: $(EDA_COLLECTION_ROOT)/galaxy.yml  $(PY_FILES) Makefile
	rm -f junipernetworks-eda-*.tar.gz
	pipenv run ansible-galaxy collection build $(EDA_COLLECTION_ROOT)
	touch "$@"

install: build
	pipenv run ansible-galaxy collection install --ignore-certs --force $(EDA_COLLECTION)

# Ignore warnings about localhost from ansible-playbook
export ANSIBLE_LOCALHOST_WARNING=False
export ANSIBLE_INVENTORY_UNPARSED_WARNING=False
