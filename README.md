# Event-Driven Ansible - k8s.eda

[![Build Status](https://github.com/Juniper/k8s.eda/workflows/Lint/badge.svg?branch=main)](https://github.com/Juniper/k8s.eda/actions?workflow=Lint)

Curated set of Event-Driven Ansible content

## Included Content

The following set of content is included within this collection:


### Event Sources 

| Name  | Description |
| ----- | ----------- |
| [junipernetworks.eda.k8s](https://github.com/Juniper/k8s.eda/blob/main/docs/k8s.eda/junipernetworks.eda.k8s_source_plugin.rst) | Respond to events within a Kubernetes cluster. |

## Usage

The following is an example of how to use the Kubernetes Event Source Plugin within an Ansible Rulebook:

```yaml
- name: Listen for newly added ConfigMap resources
  hosts: all
  sources:
    - junipernetworks.eda.k8s:
        api_version: v1
        kind: ConfigMap
  rules:
    - name: Notify
      condition: event.type == "ADDED"
      action:
        debug:                      
```

## License

Apache 2.0
