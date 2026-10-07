# Register `finance010-ubuntu22`

Two additions, both purely additive.

## 1. Environment configuration

Copy `configs/environments/environment_finance010.yaml` into the ALE checkout's
`configs/environments/`. It declares the `finance010-ubuntu22` snapshot against the **docker**
provider, sets `task_data_source: local:task-data`, and pins `network: none`.

No image module is required for this case. finance001 ships
`ale_run/environments/images/finance001_ubuntu22.py` because a gcloud snapshot has to be
described to the image registry — machine type, task-data root, CUA port, interpreter paths. A
docker-provider snapshot is resolved by the image reference in the environment YAML above, so
adding a module would be a second, redundant source of truth for the same binding. If this case
is ever moved to the gcloud provider, that module becomes required; see BUILD_SNAPSHOT.md §5.

## 2. Task package

Copy `env/packages-linux/finance010-runtime/` into the ALE checkout's `env/packages-linux/`.
The task card names `finance010-runtime` in `requiredSystemPackages`, so
`env/install_task_deps.sh` picks it up with no further registration.

## What must not change

The patch installs on top of the official `ale-ubuntu22` base. It must not replace the CUA
server, `/opt/ale-run/.venv`, the Node runtime, the desktop startup path, or the image path
conventions. `install.sh` adds apt and pip packages and one health binary
(`/usr/local/bin/finance010-runtime-health`), and nothing else.
