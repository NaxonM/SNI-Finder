import sys
from pathlib import Path

# Add project root to path so we can import from sni_finder
sys.path.append(str(Path(__file__).resolve().parent.parent))

import csv
import logging
import ipaddress
from concurrent.futures import ThreadPoolExecutor, as_completed
from sni_finder.pairs import load_cf_subnets, resolve_ips_for_sni
from sni_finder.shared import ROOT, SNI_LIST_PATH

def extract_cf_domains(tranco_csv_path: Path, limit: int = 5000, max_workers: int = 50) -> list[str]:
    print(f"Loading Cloudflare subnets...")
    cf_subnets = load_cf_subnets()
    
    print(f"Reading top {limit} domains from Tranco CSV: {tranco_csv_path}...")
    domains_to_check: list[str] = []
    with open(tranco_csv_path, "r", encoding="utf-8", errors="ignore") as f:
        reader = csv.reader(f)
        for idx, row in enumerate(reader):
            if idx >= limit:
                break
            if len(row) >= 2:
                domains_to_check.append(row[1].strip().lower())

    print(f"Resolving {len(domains_to_check)} domains in parallel with {max_workers} threads...")
    cf_domains: list[str] = []
    
    def check_domain(domain: str) -> str | None:
        ips = resolve_ips_for_sni(domain, max_ips=1)
        if not ips:
            return None
        try:
            ip_obj = ipaddress.ip_address(ips[0])
            if any(ip_obj in subnet for subnet in cf_subnets):
                return domain
        except Exception:
            pass
        return None

    completed = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(check_domain, dom): dom for dom in domains_to_check}
        for fut in as_completed(futures):
            completed += 1
            res = fut.result()
            if res:
                cf_domains.append(res)
            if completed % 250 == 0 or completed == len(domains_to_check):
                print(f"Progress: {completed}/{len(domains_to_check)} domains processed. Found {len(cf_domains)} Cloudflare domains.")

    return sorted(cf_domains)

def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Extract Cloudflare-fronted domains from a Tranco top-sites CSV list."
    )
    parser.add_argument(
        "tranco_csv",
        type=str,
        help="Path to the Tranco top-sites CSV file."
    )
    parser.add_argument(
        "limit",
        type=int,
        nargs="?",
        default=5000,
        help="Number of top domains to read from the CSV (default: 5000)."
    )

    args = parser.parse_args()
    tranco_path = Path(args.tranco_csv)
    if not tranco_path.exists():
        print(f"Error: Tranco CSV not found at {tranco_path}")
        sys.exit(1)
        
    cf_domains = extract_cf_domains(tranco_path, limit=args.limit)
    
    print(f"\nSuccessfully identified {len(cf_domains)} Cloudflare-fronted domains!")
    
    # Load current SNI list to avoid duplicates
    current_snis = set()
    if SNI_LIST_PATH.exists():
        for line in SNI_LIST_PATH.read_text(encoding="utf-8", errors="ignore").splitlines():
            if "#" in line:
                line = line.split("#", 1)[0]
            sni = line.strip().lower()
            if sni:
                current_snis.add(sni)
                
    new_domains = [d for d in cf_domains if d not in current_snis]
    print(f"Found {len(new_domains)} new Cloudflare domains not already in your SNI list.")
    
    if new_domains:
        # Append new domains to SNI list
        print(f"Appending new domains to {SNI_LIST_PATH}...")
        with open(SNI_LIST_PATH, "a", encoding="utf-8") as f:
            f.write("\n# Added from Tranco Top Sites\n")
            for dom in new_domains:
                f.write(f"{dom}\n")
        print("Done! You can now start the scanner and scan these new high-reliability domains.")
    else:
        print("No new domains to add.")


if __name__ == "__main__":
    main()
