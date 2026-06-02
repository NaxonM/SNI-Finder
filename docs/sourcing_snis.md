# Sourcing Reliable Cloudflare SNIs for DPI Circumvention

To find the best Server Name Indication (SNI) hostnames that successfully evade Deep Packet Inspection (DPI) in your specific network, you need a high-quality list of candidate domains that are hosted behind Cloudflare.

The most resilient SNIs are **highly popular, active websites** because internet service providers (ISPs) are extremely hesitant to block them due to high collateral damage (i.e. breaking access to massive public websites).

This guide explains the most effective methods to find and collect thousands of Cloudflare-fronted domains to feed into the SNI-Finder scanner.

---

## 🛠️ Method 1: The Automated Tranco Top Sites Helper (Recommended)

To make sourcing elite, high-traffic domains as easy as possible, the project includes an automated parallel processing script: **`scripts/extract_cf_from_tranco.py`**.

This script reads from a global domain rank CSV (like the Tranco Top 1 Million list), resolves the domains in parallel, matches their IP addresses against Cloudflare's exact subnet ranges, and automatically appends any new Cloudflare domains to your `config/sni-list.txt`!

### How to use it:
1. **Download the Tranco Top 1M list** from the [Tranco List Project](https://tranco-list.eu/).
2. Move the downloaded CSV file into your project folder and rename it to `tranco_334VL.csv` (or edit the script to match your file name).
3. Run the helper script (specifying the number of top domains to check, e.g. `5000`):
   ```bash
   # On Windows
   python scripts/extract_cf_from_tranco.py 5000
   
   # On Linux (make sure your virtual environment is active)
   python3 scripts/extract_cf_from_tranco.py 5000
   ```
4. The helper will automatically query DNS in parallel, filter out any duplicates already present, and append all new Cloudflare-fronted domains directly to your `config/sni-list.txt`!

---

## 👥 Method 2: Community-Maintained GitHub Lists

Censorship circumvention developers maintain active lists of known, clean Cloudflare domains specifically tailored for TLS tunneling:
* **[v2fly/domain-list-community](https://github.com/v2fly/domain-list-community)**: The most authoritative community-managed database powering global V2Ray/Xray proxy rule-routing.
  - Explore the [data](https://github.com/v2fly/domain-list-community/tree/master/data) folder. Curated lists like `category-ads-all`, `geolocation-!cn`, or `google` contain thousands of domains fronted by global CDNs.
* **IRCF Space Active Domains**: Search GitHub for `ircfspace/active-domains` or `MortezaBashirgange/CleanIP-SNI` which host raw, copy-paste friendly text lists of active Cloudflare SNIs.

---

## 🌐 Method 3: Reverse IP Lookup Databases

If you know a Cloudflare IP address, you can perform a reverse lookup to find all the domains associated with it:
1. Pick a Cloudflare IP from `config/cf_subnets.txt` (or use a popular one like `104.16.12.250` or `104.19.230.21`).
2. Visit a Reverse IP directory:
   - **[ViewDNS.info (Reverse IP Lookup)](https://viewdns.info/)**
   - **[HackerTarget (Reverse IP)](https://hackertarget.com/reverse-ip-lookup/)**
3. Input the IP address, and it will return a list of hundreds or thousands of active websites currently routed through Cloudflare.

---

## 🗺️ Method 4: Nameserver Queries (SecurityTrails / DNSdumpster)

You can target specific large-scale networks hosted on Cloudflare's Nameservers:
* **[SecurityTrails](https://securitytrails.com/)**: Search for domains hosted on Cloudflare's primary nameservers (like `*.ns.cloudflare.com`) to extract lists of active domains.
* **[DNSdumpster](https://dnsdumpster.com/)**: Search for a large Cloudflare-fronted company (e.g. `hcaptcha.com`, `cloudflare.com`, `letsenrypt.org`) to extract all of their active subdomains. Subdomains are excellent SNI candidates because they inherit Cloudflare's edge proxy properties but are less likely to draw individual traffic monitoring.
