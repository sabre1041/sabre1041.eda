===================================================
Junipernetworks EDA source Collection Release Notes
===================================================

.. contents:: Topics

v1.2.0
=======

Major Changes
-------------

- Add support for filtering MODIFIED events by specific fields.
- Add decision environment image build.

v1.1.13
=======

Minor Changes
-------------

- Updated documentation.

v1.1.12
=======

Major Changes
-------------

- Add support for watching multiple resources.
- INIT_DONE event now includes all resources from get before watch in a resource list.
  * Initiaze watches in the order they appear in the configuration.
  * Start watching all types in parallel.

v1.0.57
=======

Major Changes
-------------

- Handle pre-existing matching events on startup.
- Add heartbeat to K8s source.
- More robust handling of resource versions.
- Unit tests based on kind.
- Avoid 410 errors from watch API.
- Improve test coverage.
- Fix events not being processed when the source is started.
- Remove extra files from packaging.
- Use asynchronous Kubernetes API.
- Include stack traces upon error.
