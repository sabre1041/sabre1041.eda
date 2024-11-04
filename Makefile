EDA_COLLECTION_ROOT := .

# Get all .py files in the EDA_COLLECTION_ROOT directory
PY_FILES := $(shell find $(EDA_COLLECTION_ROOT) -name *.py)

VERSION := $(shell sed -n '/^version: / s,.*"\(.*\)"$$,\1,p' $(EDA_COLLECTION_ROOT)/galaxy.yml)

PY_VERSION := $(shell cat .python-version)

EDA_COLLECTION = junipernetworks-eda-$(VERSION).tar.gz

KIND_VERSION := v0.25.0
KUBERNETES_VERSION := v1.29.10

.PHONY: setup build release-build install clean clean-pipenv pipenv test test-bin

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
# Unix
os=linux
export LDFLAGS := -Wl,-rpath,$(shell brew --prefix openssl)/lib
export CPPFLAGS := -I$(shell brew --prefix openssl)/include
export CONFIGURE_OPTS := --with-openssl=$(shell brew --prefix openssl)
endif

# Determine the URL's for programs to download
kind_url := https://kind.sigs.k8s.io/dl/$(KIND_VERSION)/kind-$(os)-$(arch)
kubectl_url := https://dl.k8s.io/release/$(KUBERNETES_VERSION)/bin/$(os)/$(arch)/kubectl

# By default use .venv in the current directory
export PIPENV_VENV_IN_PROJECT=1

setup: clean-pipenv test-bin
	pyenv uninstall --force $(PY_VERSION)
	rm -rf $(HOME)/.pyenv/versions/$(PY_VERSION) test-bin
	$(PYENV_INSTALL_PREFIX) pyenv install --force $(PY_VERSION)
	pip install pipenv pre-commit
	$(MAKE) pipenv
	pre-commit install

define install_collection_if_missing
	pipenv run ansible-doc $(1) &>/dev/null || pipenv run ansible-galaxy collection install --ignore-certs --force $(1)
endef

pipenv:
	pipenv --help &>/dev/null || pip install pipenv
	pipenv install --dev

clean-pipenv:
	pipenv --rm || true
	PIPENV_VENV_IN_PROJECT= pipenv --rm || true
	rm -rf .venv

test-bin: test-bin/kubectl test-bin/kind

test-bin/kind:
	mkdir -p test-bin
	curl -Lso "$@" $(kind_url)
	chmod +x "$@"

test-bin/kubectl:
	mkdir -p test-bin
	curl -Lso "$@" $(kubectl_url)
	chmod +x "$@"

release-build: pipenv
	rm -f $(EDA_COLLECTION_ROOT)/.eda-collection
	make build

build: $(EDA_COLLECTION_ROOT)/.eda-collection

test: pipenv
	pipenv run pytest

$(EDA_COLLECTION_ROOT)/.eda-collection: $(EDA_COLLECTION_ROOT)/galaxy.yml  $(PY_FILES)
	rm -f junipernetworks-eda-*.tar.gz
	pipenv run ansible-galaxy collection build $(EDA_COLLECTION_ROOT)
	touch "$@"

install: build
	pipenv run ansible-galaxy collection install --ignore-certs --force $(EDA_COLLECTION)

# Ignore warnings about localhost from ansible-playbook
export ANSIBLE_LOCALHOST_WARNING=False
export ANSIBLE_INVENTORY_UNPARSED_WARNING=False
