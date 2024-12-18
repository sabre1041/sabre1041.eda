# Event-Driven Ansible - k8s.eda

[![Build Status](https://github.com/Juniper/k8s.eda/workflows/Lint/badge.svg?branch=main)](https://github.com/Juniper/k8s.eda/actions?workflow=Lint)

Curated set of Event-Driven Ansible content.

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
2. Obtain a list of existing objects that match the query upon startup via the `INIT_DONE` event.
3. Get notified upon event types `ADDED`, `MODIFIED` and `DELETED` for resources matching the filters.

This extension is implemented with the kubernetes_asyncio client, which is non-blocking, ensuring that the EDA activation will be responsive.

### Examples

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

You can also listen an any number of objects in the same rulebook activation. For [example](rulebooks/k8s_multiple.yml):

```yaml
---
- name: Listen for newly created Namespace
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
| log_level | Log level. Can be (`CRITICAL`,`ERROR`,`INFO`,`DEBUG`,`NOTSET`) | `INFO` |

## License

Apache 2.0
