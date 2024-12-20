# Event-Driven Ansible - k8s.eda

[![Build Status](https://github.com/Juniper/k8s.eda/workflows/Lint/badge.svg?branch=main)](https://github.com/Juniper/k8s.eda/actions?workflow=Lint)

Curated set of Event-Driven Ansible content.

- [Event-Driven Ansible - k8s.eda](#event-driven-ansible---k8seda)
  - [Included Content](#included-content)
    - [Event Sources](#event-sources)
  - [Usage](#usage)
    - [Overview](#overview)
    - [Examples](#examples)
      - [Watching a Single Event](#watching-a-single-event)
      - [Watching Multiple Events](#watching-multiple-events)
      - [Watching Changes on Specific Fields](#watching-changes-on-specific-fields)
  - [Configuration](#configuration)
    - [Authentication](#authentication)
    - [Authorization](#authorization)
    - [Watch Configuration](#watch-configuration)
  - [Building and Publishing a Decision Environment Image](#building-and-publishing-a-decision-environment-image)
  - [Image Build](#image-build)
    - [Image Publish](#image-publish)
  - [Development Environment](#development-environment)
    - [Setup](#setup)
      - [Mac OS X](#mac-os-x)
      - [Linux-based Systems](#linux-based-systems)
      - [All Systems](#all-systems)
    - [Usage](#usage-1)
  - [License](#license)


## Included Content

The following set of content is included within this collection:

### Event Sources

| Name  | Description |
| ----- | ----------- |
| [junipernetworks.eda.k8s](https://github.com/Juniper/k8s.eda/blob/main/docs/k8s.eda/junipernetworks.eda.k8s_source_plugin.rst) | Respond to events within a Kubernetes cluster. |

## Usage

### Overview

The `junipernetworks.eda.k8s` extension provides a reliable means for taking action upon Kubernetes events.  It provides mechanisms to:

1. Specify what kind(s) to watch with various filter, including:
   * `api_version`
   * `kind`
   * `name`
   * `namespace`
   * `label_selectors`
   * `field_selectors`
   * `changed_fields`
2. Obtain a list of existing objects that match the query upon startup via the `INIT_DONE` event.
3. Get notified upon event types `ADDED`, `MODIFIED` and `DELETED` for resources matching the filters.

This extension is implemented with the kubernetes_asyncio client, which is non-blocking, ensuring that the EDA activation will be responsive.

### Examples

#### Watching a Single Event

The following is an example of how to use the Kubernetes Event Source Plugin within an Ansible Rulebook. For [example](rulebooks/k8s.yml):

```yaml
- name: Listen for ConfigMaps across
  hosts: all
  sources:
    - junipernetworks.eda.k8s:
        api_version: v1
        kind: ConfigMap
  rules:
    - name: Existing ConfigMaps
      condition: event.type == "INIT_DONE" and event.resources.kind == "ConfigMapList"
      action:
        debug:
          msg: "INIT_DONE: ConfigMaps: {{ event.resources }}"

    - name: ConfigMap Added
      condition: event.type == "ADDED"
      action:
        debug:
          msg: "ADDED: ConfigMap {{ event.resource.metadata.namespace }}/{{ event.resource.metadata.name }}"
```

#### Watching Multiple Events

You can also listen an any number of objects in the same rulebook activation. For [example](rulebooks/k8s_multiple.yml):

```yaml
---
- name: Listen for Namespace or Pod
  hosts: all
  sources:
    - junipernetworks.eda.k8s:
        kinds:
          - api_version: v1
            kind: Namespace
          - api_version: v1
            kind: Pod
            label_selectors:
              - app: myapp
  rules:
    - name: Existing Namespaces
      condition: event.type == "INIT_DONE" and event.resources.kind == "NamespaceList"
      action:
        debug:
          msg: "INIT_DONE: Namespaces: {{ event.resources }}"

    - name: Namespace Added/Modified/Deleted
      condition: event.resource.kind == "Namespace"
      action:
        debug:
          msg: "{{ event.type }}: Namespace {{ event.resource.metadata.name }}"

    - name: Existing Pods
      condition: event.type == "INIT_DONE" and event.resources.kind == "PodList"
      action:
        debug:
          msg: "INIT_DONE: Pods: {{ event.resources }}"

    - name: Pod Added/Modified/Deleted
      condition: event.resource.kind == "Pod"
      action:
        debug:
          msg: "{{ event.type }}: Pod {{ event.resource.metadata.namespace }}/{{ event.resource.metadata.name }} with labels {{ event.resource.metadata.labels }}"
```

#### Watching Changes on Specific Fields

The event source can also be configured to monitor specific fields on a resource:

```yaml
- name: Listen for ConfigMaps across
  hosts: all
  sources:
    - junipernetworks.eda.k8s:
        api_version: v1
        kind: ConfigMap
        changed_fields:
          - data
          - metadata.annotations.foo

  rules:
    - name: Modified ConfigMap Specific Fields
      condition: event.resource.kind == "ConfigMap" and event.type == "MODIFIED"
      action:
        debug:
          msg: "{{ event.resource.metadata.name }} changed foo to {{ event.metadata.annotations.foo }}"
```

## Configuration

### Authentication

When running in a kubernetes environment, the rulebook activation pod will look for the service account secret that's typically injected into the pod. So, no configuration is required.

Otherwise, the following parameters can be specified manually:

|Key|Alias|Purpose|
|---|-----|-------|
| kubeconfig|| Path to Kuberenetes config file |
| context || Kubernetes context |
| host || Kubernetes API host |
| api_key || Authorization key for the Kubernetes API server |
| username || Kubernetes user |
| password || Kubernetes user password |
| validate_certs | verify_ssl | Boolean to specify whether SSL verification is required |
| ca_cert | ssl_ca_cert| Path to certificate authority file to used to validate cert |
| client_cert | cert_file | Path to client certificate file |
| client_key | key_file | Path to client key file |
| proxy | | URL for proxy server |
| no_proxy || Disable proxy (even if proxy is configured) |
| proxy_headers || Dictionary of proxy headers to use. See [k8s.py](plugins/event_source/k8s.py#L353) for details. |
| persist_config || Boolean to configure persistence of the client configuration. |
| impersonate_user || User to impersonate |
| impersonate_groups || List of groups to impersonate |

### Authorization

In order for the watch to work the (service) account that's associated with the authenticated user must be authorized to get, list and watch the types specified.

Here is how you might configure a [cluster role](hack/clusterrole-eda.yml):
```yaml
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: eda-service-account-role
rules:
  - apiGroups: [""]
    resources: ["namespaces", "pods"]
    verbs: ["get", "list", "watch"]
  - apiGroups: ["sriovnetwork.openshift.io"]
    resources: ["sriovnetworks"]
    verbs: ["get", "list", "watch"]
```

This is how you might configure a [cluster role binding to a service account](hack/clusterrolebinding-eda.yml):
```yaml
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: eda-service-account-binding
subjects:
  - kind: ServiceAccount
    name: default  # Replace with the name of your service account
    namespace: aap  # Replace with the namespace of your service account
roleRef:
  kind: ClusterRole
  name: eda-service-account-role
  apiGroup: rbac.authorization.k8s.io
```

### Watch Configuration

The following parameters are supported in the watch configuration.

|Config|Purpose|Default|
|------|-------|-------|
| api_version | Version of the kinds to watch | v1 |
| kind | Kind to watch | |
| kinds | List of kinds to watch. It's a list of dictionaries including all of the configuration values in this list except kinds. For each kind, it will use the top-level value as the default. For example, if namespace is set at the top level but not in one of the kind list entries, the namespace from the top will be used as a default. | |
| name | Name of the kind to watch | |
| namespace | Namespace to watch for `kind` | |
| label_selectors | Labels to filter resources | [] |
| field_selectors | Fields to filter resources | [] |
| changed_fields | Filter modified events by specific fields | [] |
| log_level | Log level. Can be (`CRITICAL`,`ERROR`,`INFO`,`DEBUG`,`NOTSET`) | `INFO` |

## Building and Publishing a Decision Environment Image

## Image Build

To build the decision environment image, docker is required.

You'll need to set the environment variables `RH_USERNAME` and `RH_PASSWORD` in the .env file at the root of your repo.  For example:

```bash
RH_USERNAME=jsmith
RH_PASSWORD=XXXXXXXXXXXXXX
```

Then `make image` will create an image named `junipernetworks-eda-de:latest`.

### Image Publish

To publish an image, you'll need to set the REGISTRY_URL in your .env file to point to the location of the docker registry you use to publish Decision Environments. For example:

```bash
REGISTRY_URL=s-artifactory.juniper.net/de
```

Then, simply run `make image` again, and in addition to rebuilding (if needed), the image `apstra-de:latest` will be tagged and pushed to the location specified in the `REGISTRY_URL`.

## Development Environment

The following tools are recommended for development of this collection:
1. [brew.sh](https://brew.sh/) -- Only needed for _Mac OS X_
1. [pyenv](https://github.com/pyenv/pyenv/blob/master/README.md)
2. [pipenv](https://github.com/pyenv/pyenv/blob/master/README.md)
3. [pre-commit](https://github.com/pre-commit/pre-commit)

### Setup

#### Mac OS X

1. If you're on a Mac and don't have brew, install it:
    ```bash
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    ```
2. If you have an ARM-based Mac, make sure the following is in your ~/.zprofile:
   ```bash
    eval "$(/opt/homebrew/bin/brew shellenv)"
   ```
   For Intel-based Mac, you may have to add this to ~/.zprofile instead:
   ```bash
    eval "$(/usr/local/bin/brew shellenv)"
   ```

3. Run the following command to install pyenv:
   ```bash
   brew install xz pyenv
   ```

4. Add this to your ~/.zprofile and restart your shell:
    ```bash
    export PYENV_ROOT="$HOME/.pyenv"
    [[ -d $PYENV_ROOT/bin ]] && export PATH="$PYENV_ROOT/bin:$PATH"
    eval "$(pyenv init -)"
    ```

#### Linux-based Systems

1. Install pyenv:

    ```bash
    curl https://pyenv.run | bash
    ```

2. To set it up in your shell follow these instructions: https://github.com/pyenv/pyenv?tab=readme-ov-file#b-set-up-your-shell-environment-for-pyenv

3. On _Ubuntu_, you'll need to install some packages to build Python properly:
      ```bash
      sudo apt -y install build-essential liblzma-dev libbz2-dev zlib1g zlib1g-dev libssl-dev libffi-dev libsqlite3-dev libncurses-dev libreadline-dev
      ```

#### All Systems

1. Download the aos-sdk, from the [Juniper Download page for Apstra](https://support.juniper.net/support/downloads/?p=apstra). Select the option for the [Apstra Automation Python 3 SDK](https://webdownload.juniper.net/swdl/dl/secure/site/1/record/179819.html?pf=Apstra%20Fabric%20Conductor). The SDK is a closed-source project. Juniper Networks is actively working to split the Apstra client code out and open-source it, as that is the only part needed for this collection.

2. The file that's downloaded will have either a 'whl' or a 'dms' extension. Just move the file to the expected location. For example: `mv ~/Downloads/aos_sdk-0.1.0-py3-none-any.dms build/wheels/aos_sdk-0.1.0-py3-none-any.whl`.

3. Run the setup `make` target:
   ```bash
   make setup
   ```

4. Optional: Follow [pipenv command completion setup instructions](https://pipenv.pypa.io/en/stable/shell.html#shell-completion). Only do it if pipenv is installed in your global Python interpreter.

### Usage

To use the development environment after setting everything up, simply run the commands:

  ```bash
  pipenv install --dev
  pipenv shell
  ```

This will start a new interactive prompt in which the known supported version of Ansible and required dependencies to use the Apstra SDK is installed.

## License

Apache 2.0
