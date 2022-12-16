# Event-Driven Ansible - sabre1041.eda

[![Build Status](https://github.com/sabre1041/sabre1041.eda/workflows/Lint/badge.svg?branch=main)](https://github.com/sabre1041/sabre1041.eda/actions?workflow=Lint)

Curated set of Event-Driven Ansible content

## Included Content

The following set of content is included within this collection:


### Event Sources 

| Name  | Description |
| ----- | ----------- |
| [sabre1041.eda.k8s](https://github.com/sabre1041/sabre1041.eda/blob/main/docs/sabre1041.eda/sabre1041.eda.k8s_source_plugin.rst) | Respond to events within a Kubernetes cluster. |

## Usage

The following is an example of how to use the Kubernetes Event Source Plugin within an Ansible Rulebook:

```yaml
- name: Listen for newly added ConfigMap resources
  hosts: all
  sources:
    - sabre1041.eda.k8s:
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