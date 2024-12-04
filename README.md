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

## Building a Decision Environment that includes this library
The instructions provided here are for Automation Platform 2.5

For Automation Platform 2.4, replace `ansible-automation-platform-25` with `ansible-automation-platform-24` and remove the `additional_build_steps` section (that is not needed)

Install `ansible-builder` on a Linux box (preferably Fedora or RHEL)
```
dnf install ansible-builder
```

Install `podman` so that `ansible-builder` can build the image (otherwise it only generates the Containerfile)
```
dnf install podman
```

Create an `ansible-builder` source file to generate the Decision Environment and call it `eda-de-openshift-aap25.yaml`
```yaml
version: 3

images:
  base_image:
    name: 'registry.redhat.io/ansible-automation-platform-25/de-minimal-rhel8:latest'

dependencies:
  galaxy:
    collections:
      - ansible.eda
      - sabre1041.eda
  python_interpreter:
    package_system: "python311"
  system:
    - pkgconfig [platform:rpm]
    - systemd-devel [platform:rpm]
    - gcc [platform:rpm]
    - python3.11-devel [platform:rpm]

options:
  package_manager_path: /usr/bin/microdnf

additional_build_steps:
  append_final:
  # This is a workaround for the bug: https://issues.redhat.com/browse/AAP-32856
    - ENV PYTHONPATH=$PYTHONPATH:/usr/local/lib/python3.11/site-packages:/usr/local/lib64/python3.11/site-packages
```

Build the image
```
ansible-builder build -f eda-de-openshift-aap25.yaml --container-runtime podman -v3 --squash all --prune-images -t eda-de-openshift-aap25:0.1.0
```

## License

Apache 2.0
