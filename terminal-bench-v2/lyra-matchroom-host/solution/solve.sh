#!/bin/bash
set -euo pipefail

for group in lyra-score lyra-chief lyra-audit lyra-emergency; do
    getent group "$group" >/dev/null || groupadd "$group"
done

ensure_user() {
    local user="$1"
    local groups="$2"
    getent group "$user" >/dev/null || groupadd "$user"
    if ! id "$user" >/dev/null 2>&1; then
        useradd -m -d "/home/$user" -s /bin/bash -g "$user" "$user"
    else
        usermod -d "/home/$user" -s /bin/bash -g "$user" "$user"
        mkdir -p "/home/$user"
        chown "$user:$user" "/home/$user"
    fi
    usermod -G "$groups" "$user"
    usermod -L "$user" >/dev/null 2>&1 || true
}

ensure_user score01 lyra-score
ensure_user score02 lyra-score
ensure_user chief01 lyra-chief,lyra-score,lyra-emergency
ensure_user auditor01 lyra-audit
ensure_user guest01 ""

mkdir -p /srv/lyra/incoming /srv/lyra/sealed /srv/lyra/rulings /srv/lyra/audit
chown root:lyra-score /srv/lyra/incoming
chown root:lyra-chief /srv/lyra/sealed /srv/lyra/rulings
chown root:lyra-audit /srv/lyra/audit
chmod 2770 /srv/lyra/incoming
chmod 2750 /srv/lyra/sealed
chmod 2770 /srv/lyra/rulings
chmod 0750 /srv/lyra/audit
setfacl -m d:g:lyra-chief:rwx,d:o::--- /srv/lyra/incoming

cat > /etc/sudoers.d/lyra-matchroom <<'EOF'
%lyra-chief ALL=(root) NOPASSWD: /usr/local/sbin/lyra-seal
%lyra-emergency ALL=(root) NOPASSWD: /usr/bin/systemctl restart ssh
score01 ALL=(ALL) !ALL
score02 ALL=(ALL) !ALL
EOF
chmod 0440 /etc/sudoers.d/lyra-matchroom

cat > /usr/local/sbin/lyra-seal <<'EOF'
#!/bin/bash
set -euo pipefail

incoming=/srv/lyra/incoming
sealed=/srv/lyra/sealed
audit_dir=/srv/lyra/audit
audit_log=$audit_dir/seal.log
ledger=$audit_dir/seal-ledger.tsv
lock=/run/lyra-seal.lock

prepare_audit() {
    mkdir -p "$audit_dir"
    chown root:lyra-audit "$audit_dir"
    chmod 0750 "$audit_dir"
    touch "$audit_log"
    chown root:lyra-audit "$audit_log"
    chmod 0640 "$audit_log"
    touch "$ledger"
    chown root:lyra-audit "$ledger"
    chmod 0640 "$ledger"
}

audit() {
    local action="$1"
    local artifact="$2"
    local detail="$3"
    prepare_audit
    printf '%s action=%s artifact=%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$action" "$artifact" "$detail" >> "$audit_log"
}

reject() {
    local artifact="$1"
    local reason="$2"
    audit rejected "$artifact" "reason=$reason"
    printf 'lyra-seal: %s\n' "$reason" >&2
    exit 1
}

if [ "$#" -ne 1 ]; then
    reject INVALID_NAME "invalid-basename"
fi

name="$1"
if [[ -z "$name" || "$name" == "." || "$name" == ".." || ! "$name" =~ ^[A-Za-z0-9._-]+$ ]]; then
    reject INVALID_NAME "invalid-basename"
fi

mkdir -p /run
exec 9>"$lock"
flock -x 9

src=$incoming/$name
dst=$sealed/$name
ruling=$(/usr/bin/printf '%s/%s.ruling' /srv/lyra/rulings "$name")

if [ ! -e "$src" ] && [ ! -L "$src" ] && [ -f "$dst" ]; then
    digest=$(sha256sum "$dst" | awk '{print $1}')
    audit already-sealed "$name" "sha256=$digest"
    exit 0
fi

if [ ! -e "$ruling" ]; then
    reject "$name" "ruling-missing"
fi

if [ -L "$ruling" ] || [ ! -f "$ruling" ] || [ "$(stat -c %h "$ruling")" -ne 1 ]; then
    reject "$name" "ruling-invalid"
fi

r_owner=$(stat -c %U "$ruling")
r_group=$(stat -c %G "$ruling")
r_mode=$(stat -c %a "$ruling")
if [[ ! "$r_owner" =~ ^(root|chief01)$ ]] || [ "$r_group" != "lyra-chief" ] || (( (8#$r_mode & 0022) != 0 )); then
    reject "$name" "ruling-owner"
fi

mapfile -t ruling_lines < "$ruling"
if [ "${#ruling_lines[@]}" -ne 5 ]; then
    reject "$name" "ruling-invalid"
fi

artifact_field=${ruling_lines[0]#artifact=}
ruling_owner=${ruling_lines[1]#owner=}
ruling_hash=${ruling_lines[2]#sha256=}
expires=${ruling_lines[3]#expires=}
nonce=${ruling_lines[4]#nonce=}
if [ "${ruling_lines[0]}" != "artifact=$artifact_field" ] \
    || [ "${ruling_lines[1]}" != "owner=$ruling_owner" ] \
    || [ "${ruling_lines[2]}" != "sha256=$ruling_hash" ] \
    || [ "${ruling_lines[3]}" != "expires=$expires" ] \
    || [ "${ruling_lines[4]}" != "nonce=$nonce" ] \
    || [ "$artifact_field" != "$name" ] \
    || [[ ! "$ruling_owner" =~ ^(score01|score02|chief01)$ ]] \
    || [[ ! "$ruling_hash" =~ ^[0-9a-f]{64}$ ]] \
    || [[ ! "$expires" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$ ]] \
    || [[ ! "$nonce" =~ ^[0-9a-f]{16}$ ]]; then
    reject "$name" "ruling-invalid"
fi

now=$(date -u +%Y-%m-%dT%H:%M:%SZ)
if [[ "$expires" < "$now" || "$expires" == "$now" ]]; then
    reject "$name" "ruling-expired"
fi

if [ -e "$dst" ] || [ -L "$dst" ]; then
    reject "$name" "destination-exists"
fi

if [ -L "$src" ]; then
    reject "$name" "source-is-symlink"
fi

if [ ! -e "$src" ]; then
    reject "$name" "source-missing"
fi

if [ ! -f "$src" ]; then
    reject "$name" "source-not-regular-file"
fi

if [ "$(stat -c %h "$src")" -ne 1 ]; then
    reject "$name" "source-has-hardlinks"
fi

owner=$(stat -c %U "$src")
case "$owner" in
    score01|score02|chief01) ;;
    *) reject "$name" "source-wrong-owner" ;;
esac

if [ "$owner" != "$ruling_owner" ]; then
    reject "$name" "ruling-invalid"
fi

mode=$(stat -c %a "$src")
if (( (8#$mode & 0002) != 0 )); then
    reject "$name" "source-other-writable"
fi

digest=$(sha256sum "$src" | awk '{print $1}')
if [ "$digest" != "$ruling_hash" ]; then
    reject "$name" "hash-mismatch"
fi
mv -T -- "$src" "$dst"
chown root:lyra-chief "$dst"
chmod 0640 "$dst"
audit sealed "$name" "sha256=$digest"
prepare_audit
timestamp=$(date -u +%Y-%m-%dT%H:%M:%SZ)
if [ -s "$ledger" ]; then
    sequence=$(($(tail -n 1 "$ledger" | cut -f1) + 1))
    prev=$(tail -n 1 "$ledger" | cut -f8)
else
    sequence=1
    prev=0000000000000000000000000000000000000000000000000000000000000000
fi
prefix=$(printf '%s\t%s\t%s\t%s\t%s\t%s\t%s' "$sequence" "$timestamp" "$name" "$owner" "$digest" "$nonce" "$prev")
entry=$(printf '%s' "$prefix" | sha256sum | awk '{print $1}')
printf '%s\t%s\n' "$prefix" "$entry" >> "$ledger"
EOF
chown root:lyra-chief /usr/local/sbin/lyra-seal
chmod 0750 /usr/local/sbin/lyra-seal

mkdir -p /etc/ssh/sshd_config.d
cat > /etc/ssh/sshd_config.d/80-lyra-matchroom.conf <<'EOF'
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
AllowGroups lyra-score lyra-chief lyra-audit
Match Group lyra-audit
PermitTTY no
AllowTcpForwarding no
X11Forwarding no
EOF

mkdir -p /etc/security/limits.d /etc/tmpfiles.d /etc/sysctl.d
cat > /etc/security/limits.d/60-lyra-matchroom.conf <<'EOF'
@lyra-score hard maxlogins 2
@lyra-chief hard maxlogins 1
@lyra-audit hard maxlogins 1
EOF

cat > /etc/tmpfiles.d/lyra-matchroom.conf <<'EOF'
d /srv/lyra/incoming 2770 root lyra-score - -
d /srv/lyra/sealed 2750 root lyra-chief - -
d /srv/lyra/rulings 2770 root lyra-chief - -
d /srv/lyra/audit 0750 root lyra-audit - -
EOF

cat > /etc/sysctl.d/90-lyra-matchroom.conf <<'EOF'
fs.protected_hardlinks = 1
fs.protected_symlinks = 1
kernel.yama.ptrace_scope = 1
EOF

cat > /etc/logrotate.d/lyra-matchroom <<'EOF'
/srv/lyra/audit/seal.log /srv/lyra/audit/seal-ledger.tsv {
    weekly
    rotate 12
    missingok
    notifempty
    compress
    create 0640 root lyra-audit
    su root lyra-audit
}
EOF
chmod 0644 /etc/logrotate.d/lyra-matchroom
