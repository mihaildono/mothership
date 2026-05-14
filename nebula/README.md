# Nebula Overlay Network — Setup Guide

Nebula creates an encrypted peer-to-peer overlay network between the mother and all child nodes.
No accounts, no SaaS — just two commands.

## Overlay Addresses

| Node      | Nebula IP |
|-----------|-----------|
| mother    | 10.10.0.1 |
| child-001 | 10.10.0.2 |
| child-002 | 10.10.0.3 |

---

## Mother setup (run once)

On the **mother machine**, from the `nebula/` directory:

```bash
./setup-mother.sh <YOUR_PUBLIC_IP>
```

Replace `<YOUR_PUBLIC_IP>` with the IP address children will use to reach this machine
(your LAN IP if all machines are on the same network; your public IP otherwise).

To generate certs for multiple children at once:

```bash
./setup-mother.sh 203.0.113.42 --children child-001,child-002,child-003
```

This will:
- Download the correct Nebula binary for your OS automatically
- Create a Certificate Authority
- Sign certificates for the mother and each child
- Install everything into `/etc/nebula/`
- Start Nebula as a background service (launchd on macOS, systemd on Linux)
- Output a self-contained bundle for each child in `nebula/bundles/`

---

## Child setup

After `setup-mother.sh` finishes, it prints the exact commands to run.
The flow is:

**1. Transfer the bundle to the child machine:**
```bash
scp nebula/bundles/child-001.tar.gz user@<child-host>:~/
```

**2. On the child machine — extract and run:**
```bash
tar -xzf child-001.tar.gz
cd child-001
./install.sh
```

`install.sh` will:
- Download the correct Nebula binary if needed (handles different OS/arch automatically)
- Install certs and config into `/etc/nebula/`
- Start Nebula as a background service
- Test connectivity to the mother (ping 10.10.0.1)
- Print the `config.toml` change needed for the mothership child agent

**3. Update `child/config.toml`:**
```toml
[mother]
host    = "10.10.0.1"
ws_port = 8765
```

---

## Firewall / router

The mother needs **UDP port 4242** reachable from the internet (or LAN):

```bash
# Linux
sudo ufw allow 4242/udp

# macOS — allow in System Settings > Network > Firewall
# or add an exception for /usr/local/bin/nebula
```

If the mother is behind a router, add a UDP port-forward for 4242 to the mother's LAN IP.
Children do not need any open ports — they connect outbound.

---

## Add a new child later

```bash
./setup-mother.sh <PUBLIC_IP> --children child-002
```

This skips the CA and any existing certs, signs only the new child cert, and creates a new bundle.
No changes needed to existing nodes.

---

## Troubleshooting

**Check Nebula is running:**
```bash
# macOS
sudo launchctl list | grep nebula

# Linux
sudo systemctl status nebula

# Logs
tail -f /var/log/nebula.log
```

**Test overlay connectivity:**
```bash
ping 10.10.0.1   # from child → mother
ping 10.10.0.2   # from mother → child
```
