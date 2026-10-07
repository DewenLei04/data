# Build `finance001-ubuntu22`

Run these steps in an ALE checkout with GCP credentials. Replace the project, zone and
repository paths with operator values. The builder must start from the official
`ale-ubuntu22` image so the resulting disk retains CUA server and ALE runtime paths.

## 1. Create the builder

```bash
gcloud compute instances create ale-finance001-builder \
  --project="$GCP_PROJECT" \
  --zone="$GCP_ZONE" \
  --image=ale-ubuntu22 \
  --machine-type=e2-standard-4
```

Copy the generated submission's `tasks/`, `task-data/`, and `environment-patch/` to an
ALE checkout on that VM.

## 2. Apply repository additions and install the task package

```bash
cp -a environment-patch/env/packages-linux/finance001-runtime \
  env/packages-linux/
cp environment-patch/ale_run/environments/images/finance001_ubuntu22.py \
  ale_run/environments/images/
cp environment-patch/configs/environments/environment_finance001.yaml \
  configs/environments/
# Apply the two registry lines documented in environment-patch/REGISTRY_PATCH.md.

sudo env/install_task_deps.sh \
  tasks/business_finance/series_b_transaction_review/task_card.json
sudo env/verify_task_env.sh \
  tasks/business_finance/series_b_transaction_review/task_card.json \
  task-data/business_finance/series_b_transaction_review/base
```

## 3. Bake task data with an encrypted reference

Set `ALE_REFERENCE_ARCHIVE_PASSWORD` in the operator shell. Do not copy this value into
the image, task card, runtime source, or Agent environment.

```bash
test -n "$ALE_REFERENCE_ARCHIVE_PASSWORD"
TASK_DATA_ROOT=/media/user/data/agenthle/business_finance/series_b_transaction_review/base
sudo install -d -m 0755 "$TASK_DATA_ROOT"
sudo cp -a task-data/business_finance/series_b_transaction_review/base/input \
  "$TASK_DATA_ROOT/input"
sudo cp -a task-data/business_finance/series_b_transaction_review/base/software \
  "$TASK_DATA_ROOT/software"
sudo cp -a task-data/business_finance/series_b_transaction_review/base/reference \
  "$TASK_DATA_ROOT/reference"
(
  cd "$TASK_DATA_ROOT/reference"
  sudo 7z a -mhe=on -p"$ALE_REFERENCE_ARCHIVE_PASSWORD" \
    "$TASK_DATA_ROOT/reference.7z" .
)
sudo rm -rf -- "$TASK_DATA_ROOT/reference"
sudo test -f "$TASK_DATA_ROOT/reference.7z"
sudo test ! -e "$TASK_DATA_ROOT/reference"
```

Use ALE's normal data-staging smoke test to confirm `input/` and `software/` are present
before the Agent and that `reference/` appears only after evaluation staging.

## 4. Verify the live builder

```bash
/usr/local/bin/finance001-services-health --check-installed
sudo systemctl start finance001-ledgerline.service finance001-stirling.service
/usr/local/bin/finance001-services-health
curl -fsS --noproxy '*' http://127.0.0.1:8091/healthz
curl -fsS --noproxy '*' http://127.0.0.1:8090/ >/dev/null
# Also run ALE's CUA endpoint and task-environment verification for this image.
```

## 5. Create and register the provider image

```bash
gcloud compute instances stop ale-finance001-builder \
  --project="$GCP_PROJECT" --zone="$GCP_ZONE"
gcloud compute images create finance001-ubuntu22 \
  --project="$GCP_PROJECT" \
  --source-disk=ale-finance001-builder \
  --source-disk-zone="$GCP_ZONE" \
  --family=finance001-ubuntu22
```

Install the supplied image module and environment YAML on the ALE host, then run:

```bash
uv run python -m ale_run list
uv run python -m ale_run run my_exp.yaml \
  --task business_finance/series_b_transaction_review --dry-run
uv run python -m ale_run run my_exp.yaml \
  --task business_finance/series_b_transaction_review
```
