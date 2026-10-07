#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FILES_DIR="$SCRIPT_DIR/files"
export DEBIAN_FRONTEND=noninteractive

required_files=(
  OpenJDK25U-jre_x64_linux_hotspot_25.0.4_7.tar.gz
  Stirling-PDF-server.jar
  jre25.sha256
  Stirling-PDF-server.jar.sha256
  ledgerline_server.py
  requirements.txt
)
for name in "${required_files[@]}"; do
  test -f "$FILES_DIR/$name" || { echo "missing package asset: $name" >&2; exit 1; }
done
test -d "$FILES_DIR/ledgerline-snapshot" || { echo "missing Ledgerline snapshot" >&2; exit 1; }

apt-get update
apt-get install -y --no-install-recommends \
  ca-certificates curl ghostscript img2pdf jq libreoffice-calc libreoffice-writer \
  ocrmypdf poppler-utils python3 python3-pip qpdf sudo tesseract-ocr \
  tesseract-ocr-eng
rm -rf /var/lib/apt/lists/*

python3 -m pip install --no-cache-dir -r "$FILES_DIR/requirements.txt"

(
  cd "$FILES_DIR"
  sha256sum -c jre25.sha256
  sha256sum -c Stirling-PDF-server.jar.sha256
)

rm -rf /opt/jre25
install -d -m 0755 /opt/jre25 /opt/stirling /opt/ledgerline
tar -xzf "$FILES_DIR/OpenJDK25U-jre_x64_linux_hotspot_25.0.4_7.tar.gz" \
  -C /opt/jre25 --strip-components=1
install -m 0444 "$FILES_DIR/Stirling-PDF-server.jar" /opt/stirling/Stirling-PDF-server.jar
install -m 0440 "$FILES_DIR/ledgerline_server.py" /opt/ledgerline/ledgerline_server.py
rm -rf /opt/ledgerline/snapshot
cp -a "$FILES_DIR/ledgerline-snapshot" /opt/ledgerline/snapshot

if ! id finance001-svc >/dev/null 2>&1; then
  useradd --system --user-group --home-dir /var/lib/finance001 --create-home \
    --shell /usr/sbin/nologin finance001-svc
fi
install -d -o finance001-svc -g finance001-svc -m 0750 \
  /var/lib/finance001 /var/lib/finance001/stirling
chown -R root:finance001-svc /opt/stirling /opt/ledgerline
find /opt/ledgerline/snapshot -type d -exec chmod 0700 {} +
find /opt/ledgerline/snapshot -type f -exec chmod 0600 {} +
chown -R finance001-svc:finance001-svc /opt/ledgerline/snapshot

printf '%s\n' 'finance001-ledgerline-key' > /etc/ledgerline_api_key
chown root:root /etc/ledgerline_api_key
chmod 0444 /etc/ledgerline_api_key

cat > /usr/local/bin/start-finance001-stirling <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
cd /var/lib/finance001/stirling
exec /opt/jre25/bin/java -jar /opt/stirling/Stirling-PDF-server.jar \
  --server.address=127.0.0.1 --server.port=8090
EOF

cat > /usr/local/bin/start-finance001-ledgerline <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
exec /usr/bin/python3 /opt/ledgerline/ledgerline_server.py \
  --snapshot /opt/ledgerline/snapshot --host 127.0.0.1 --port 8091
EOF
chmod 0755 /usr/local/bin/start-finance001-stirling /usr/local/bin/start-finance001-ledgerline

cat > /usr/local/bin/libreoffice <<'EOF'
#!/usr/bin/env bash
# ALE-DEPS-SHIM: the ALE image's global library path breaks LibreOffice UNO bootstrap.
unset LD_LIBRARY_PATH
exec /usr/lib/libreoffice/program/soffice "$@"
EOF
chmod 0755 /usr/local/bin/libreoffice

cat > /etc/systemd/system/finance001-stirling.service <<'EOF'
[Unit]
Description=finance001 Stirling-PDF loopback service
After=network.target

[Service]
Type=simple
User=finance001-svc
Group=finance001-svc
WorkingDirectory=/var/lib/finance001/stirling
Environment=DOCKER_ENABLE_SECURITY=false
Environment=SYSTEM_ENABLEANALYTICS=false
ExecStart=/usr/local/bin/start-finance001-stirling
Restart=always
RestartSec=2
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=/var/lib/finance001/stirling

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/finance001-ledgerline.service <<'EOF'
[Unit]
Description=finance001 Ledgerline loopback service
After=network.target

[Service]
Type=simple
User=finance001-svc
Group=finance001-svc
ExecStart=/usr/local/bin/start-finance001-ledgerline
Restart=always
RestartSec=2
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadOnlyPaths=/opt/ledgerline

[Install]
WantedBy=multi-user.target
EOF

cat > /usr/local/bin/finance001-services-health <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
if [[ "${1:-}" == "--check-installed" ]]; then
  test -x /opt/jre25/bin/java
  test -r /opt/stirling/Stirling-PDF-server.jar
  test -r /opt/ledgerline/ledgerline_server.py
  systemctl is-enabled finance001-stirling.service >/dev/null
  systemctl is-enabled finance001-ledgerline.service >/dev/null
  exit 0
fi

if ! curl -fsS --noproxy '*' http://127.0.0.1:8091/healthz >/dev/null 2>&1; then
  sudo -n /usr/bin/systemctl restart finance001-ledgerline.service
fi
if ! curl -fsS --noproxy '*' http://127.0.0.1:8090/ >/dev/null 2>&1; then
  sudo -n /usr/bin/systemctl restart finance001-stirling.service
fi
for _ in $(seq 1 180); do
  if curl -fsS --noproxy '*' http://127.0.0.1:8091/healthz >/dev/null 2>&1 \
    && curl -fsS --noproxy '*' http://127.0.0.1:8090/ >/dev/null 2>&1; then
    echo SERVICES_OK
    exit 0
  fi
  sleep 1
done
systemctl --no-pager --full status finance001-ledgerline.service finance001-stirling.service >&2 || true
exit 1
EOF
chmod 0755 /usr/local/bin/finance001-services-health

cat > /etc/sudoers.d/finance001-services <<'EOF'
user ALL=(root) NOPASSWD: /usr/bin/systemctl restart finance001-stirling.service
user ALL=(root) NOPASSWD: /usr/bin/systemctl restart finance001-ledgerline.service
EOF
chmod 0440 /etc/sudoers.d/finance001-services
visudo -cf /etc/sudoers.d/finance001-services

systemctl daemon-reload
systemctl enable finance001-stirling.service finance001-ledgerline.service
/opt/jre25/bin/java -version
/usr/local/bin/finance001-services-health --check-installed
