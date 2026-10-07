# Build `finance010-ubuntu22`

The snapshot is a Docker image. Build it from the official `ale-ubuntu22` base so the result
retains the CUA server, ALE runtime paths, Node runtime and desktop startup; the task package
below is additive on top of that.

Steps 1-4 need network (apt, PyPI and Playwright's Chromium download). The task itself runs with
`network: none`.

## 1. Stage the patch into an ALE checkout

```bash
cp -a environment-patch/env/packages-linux/finance010-runtime env/packages-linux/
cp environment-patch/configs/environments/environment_finance010.yaml configs/environments/
# No image-module edit is needed for the docker provider; see REGISTRY_PATCH.md.
```

## 2. Install the task package and verify the task environment

```bash
sudo env/install_task_deps.sh \
  tasks/business_finance/bond_credit_ma_purchase_price/task_card.json
sudo env/verify_task_env.sh \
  tasks/business_finance/bond_credit_ma_purchase_price/task_card.json \
  task-data/business_finance/bond_credit_ma_purchase_price/base
```

`install.sh` ends by running `/usr/local/bin/finance010-runtime-health --check-installed`, so a
missing converter or a font that resolves outside the Liberation/Carlito set fails the install
rather than the run.

## 3. Commit the image

```bash
docker commit <container> finance010-ubuntu22:latest
```

The image reference in `configs/environments/environment_finance010.yaml` is
`finance010-ubuntu22:latest`. Keep the two in step.

The case also has its own lean `ubuntu:22.04` Dockerfile, used during authoring on a host with
no ALE base image available. It is deliberately **not** shipped in this patch: it starts from
plain Ubuntu, so it has no CUA server and no agent can be driven inside it, and its `COPY` lines
resolve only against the authoring repository. Build the submission image from `ale-ubuntu22` as
above; `install.sh` is self-contained and reads only its own `files/` directory.

## 4. Stage task data, in this order

The order is the whole secrecy guarantee for this case. `input/` and `software/` are staged
before the agent; `reference/` is staged only after the agent exits. Nothing is encrypted,
because nothing private is ever inside the container while the agent runs.

```bash
TASK_DATA_ROOT=<task-data root>/business_finance/bond_credit_ma_purchase_price/base
sudo install -d -m 0755 "$TASK_DATA_ROOT"
sudo cp -a task-data/business_finance/bond_credit_ma_purchase_price/base/input    "$TASK_DATA_ROOT/input"
sudo cp -a task-data/business_finance/bond_credit_ma_purchase_price/base/software "$TASK_DATA_ROOT/software"
# reference/ is NOT copied here. It is staged by the harness after the agent exits.
sudo test ! -e "$TASK_DATA_ROOT/reference"
```

The commands above are the whole staging contract. The case has a host-side script that performs
the same sequence and asserts the pre-agent window is clean, but it calls the authoring
repository's own gate to do so, so it is not shippable and is not included here.

The adapter enforces the same boundary from its own side: `start` deletes the reference directory
and then asserts it is empty, and `software/verify_inputs.py` re-checks that no private artifact
is reachable from the input tree and that the staged corpus matches, byte for byte, the inventory
recorded in the task card at build time.

## 5. Verify the installed task

```bash
uv run python -m ale_run list
uv run python -m ale_run run my_exp.yaml \
  --task business_finance/bond_credit_ma_purchase_price --dry-run
uv run python -m ale_run run my_exp.yaml \
  --task business_finance/bond_credit_ma_purchase_price
```

The final command needs an operator-built image and provider credentials; those external
operations are intentionally not emulated by the local package builder.

If this case is ever moved to the **gcloud** provider, three things change: build a VM from the
`ale-ubuntu22` image and `gcloud compute images create finance010-ubuntu22` from its disk; add an
`ale_run/environments/images/finance010_ubuntu22.py` module (see finance001's for the shape); and
either switch `task_data_source` to a baked-in mode **with an encrypted reference archive**, or
keep `local:task-data`. Do not bake an unencrypted `reference/` into a published image — that is
the exact failure the ordering above exists to prevent.
