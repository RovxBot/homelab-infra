Recommended Priorities

- Fix the NAS link first: inspect/replace the cable on UniFi switch port 6. It reports 100 Mbps, errors, and repeated disconnects; verify both aggregation members return to 1 Gbps.
- Measure latency under load: test wired download/upload performance before enabling Smart Queues. Tune against Starlink’s sustained speeds—not its advertised peak—to balance latency and throughput.
- Tune Wi-Fi conservatively: resolve overlapping 5 GHz channel blocks and investigate weak client signals. Keep 2.4 GHz at 20 MHz; avoid blanket power increases.

Isolation Without Breaking Your Home

- IoT VLAN: move appliances such as the fridge off trusted Wi-Fi. Block unsolicited access to trusted networks and router/switch administration, while allowing required Home Assistant control and callbacks.
- Media VLAN: place casting devices separately, with explicit access to Jellyfin and trusted phones. Relay only necessary discovery services; test casting before migrating everything. UniFi discovery guidance (https://help.ui.com/hc/en-us/articles/12648701398807-UniFi-Gateway-Multicast-DNS-mDNS-Proxy)
- Stabilise Home Assistant’s network identity: it can currently reschedule between nodes. Establish a reliable addressing/scheduling strategy before relying on a single-IP firewall exception.
- Leave Kubernetes and cameras in place initially: migrate them only after mapping storage, recording, and application dependencies.

Guests And Privacy

- Guest QR: use encrypted guest Wi-Fi with a joining QR, retain the voucher portal’s 8-hour expiry, internet-only access, client isolation, and bandwidth limits. The QR joins Wi-Fi; the voucher authorises internet access. Guest portal controls (https://help.ui.com/hc/en-us/articles/115000166827-UniFi-Hotspots-and-Captive-Portals)
- Harden management: enable HTTPS/SSH on Dell switches, verify access, then disable HTTP. Restrict management to designated trusted devices.
- DNS privacy: evaluate encrypted upstream DNS and optional tracker blocking, retaining local DNS resolution. This does not make browsing anonymous.
- Keep UPnP disabled, review remote-access needs, and remove temporary accounts afterward.