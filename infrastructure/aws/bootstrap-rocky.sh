#!/usr/bin/env bash
set -euo pipefail
exec > >(tee -a /var/log/language-benchmark-bootstrap.log) 2>&1
if [ "${SKIP_UPGRADE:-0}" != 1 ]; then dnf -y upgrade; fi
dnf -y install dnf-plugins-core curl git tar unzip zstd python3 gnupg2 chrony policycoreutils-python-utils
systemctl enable --now chronyd
dnf -y install kernel-modules-extra
latest_kernel=$(rpm -q kernel-core --qf '%{VERSION}-%{RELEASE}.%{ARCH}\n' | sort -V | tail -1)
if [ "$(uname -r)" != "$latest_kernel" ] && [ "${SKIP_UPGRADE:-0}" != 1 ]; then
  cp "$0" /usr/local/sbin/language-benchmark-bootstrap
  chmod 700 /usr/local/sbin/language-benchmark-bootstrap
  cat > /etc/systemd/system/language-benchmark-bootstrap.service <<'UNIT'
[Unit]
After=network-online.target
Wants=network-online.target
ConditionPathExists=!/var/lib/language-benchmark/bootstrap-complete
[Service]
Type=oneshot
ExecStart=/usr/bin/env SKIP_UPGRADE=1 /usr/local/sbin/language-benchmark-bootstrap
[Install]
WantedBy=multi-user.target
UNIT
  systemctl enable language-benchmark-bootstrap.service
  shutdown -r +1 'Boot updated Rocky kernel before Docker setup'
  exit 0
fi
curl -fsSL https://download.docker.com/linux/rhel/docker-ce.repo -o /etc/yum.repos.d/docker-ce.repo
curl -fsSL https://download.docker.com/linux/rhel/gpg -o /tmp/docker.gpg
gpg --show-keys --with-colons /tmp/docker.gpg | grep -q '060A61C51B558A7F742B77AAC52FEB6B621E9F35'
rpm --import /tmp/docker.gpg
dnf -y remove podman runc || true
dnf -y install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker
usermod -aG docker rocky
curl -fsSL https://astral.sh/uv/install.sh -o /tmp/uv-install.sh
su - rocky -c 'sh /tmp/uv-install.sh'
curl -fsSL https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip -o /tmp/aws.zip
unzip -q /tmp/aws.zip -d /tmp
/tmp/aws/install --update
# Native PostgreSQL clients must be at least the server's major version.
dnf -y install https://download.postgresql.org/pub/repos/yum/reporpms/EL-10-x86_64/pgdg-redhat-repo-latest.noarch.rpm
dnf -y install postgresql17
ln -sf /usr/pgsql-17/bin/pg_dump /usr/local/bin/pg_dump
ln -sf /usr/pgsql-17/bin/pg_restore /usr/local/bin/pg_restore
ln -sf /usr/pgsql-17/bin/psql /usr/local/bin/psql
# No automatic package upgrades during a measured campaign.
systemctl disable --now dnf-automatic.timer 2>/dev/null || true
mkdir -p /var/lib/language-benchmark
cat /etc/rocky-release > /var/lib/language-benchmark/versions.txt
docker version >> /var/lib/language-benchmark/versions.txt
docker compose version >> /var/lib/language-benchmark/versions.txt
rpm -q docker-ce docker-ce-cli containerd.io docker-compose-plugin >> /var/lib/language-benchmark/versions.txt
touch /var/lib/language-benchmark/bootstrap-complete
