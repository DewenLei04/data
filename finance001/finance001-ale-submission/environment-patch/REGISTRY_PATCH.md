# Register `finance001-ubuntu22`

Copy `finance001_ubuntu22.py` into `ale_run/environments/images/`, then update
`ale_run/environments/images/__init__.py`:

```python
from .finance001_ubuntu22 import IMAGE as _FINANCE001_UBUNTU22

_REGISTRY: dict[str, Image] = {
    # existing image families...
    _FINANCE001_UBUNTU22.name: _FINANCE001_UBUNTU22,
}
```

Build the VM from the official `ale-ubuntu22` image, apply
`env/packages-linux/finance001-runtime/install.sh` through ALE's
`env/install_task_deps.sh`, bake the task data using the platform reference archive
password, and create the provider image named `finance001-ubuntu22`. Do not start from
plain Ubuntu: the official base supplies CUA server, framework Python, Node, desktop
runtime, and the registered filesystem paths used by the module above.

