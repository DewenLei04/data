"""finance001 image family built as an additive snapshot of ale-ubuntu22."""

from __future__ import annotations

from . import Image


IMAGE = Image(
    name="finance001-ubuntu22",
    os="linux",
    work_dir_base="/home/user/.ale",
    task_data_root="/media/user/data/agenthle",
    node="/usr/local/bin/node",
    python="/opt/ale-run/.venv/bin/python",
    mcp_server_dir="/home/user/cua_mcp_server",
    default_machine_type="e2-standard-4",
    cua_server_port=5000,
)

