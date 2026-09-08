### Tailscale status after the rollout
# host: ip-172-31-41-235, utc: 2026-09-08T12:17:59Z
$ tailscale status

100.100.229.4  ec2-api                 ec2-api.tail422656.ts.net  linux  -                                                     
100.95.254.13  alexanders-macbook-pro  alexander-constanza@       macOS  active; direct 185.43.230.52:1712, tx 52584 rx 36872  
100.118.66.61  apihealthchecker-fly    tagged-devices             linux  -                                                     

# Funnel on:
#     - https://ec2-api.tail422656.ts.net


### Advertised and accepted routes
# host: ip-172-31-41-235, utc: 2026-09-08T12:17:59Z
$ sh -c tailscale status --json | grep -i -A3 "AdvertisedRoutes\|PrimaryRoutes" || true

    "PrimaryRoutes": [
      "172.31.0.0/16"
    ],
    "Addrs": [

