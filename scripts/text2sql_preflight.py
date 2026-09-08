"""Inspect prerequisites without opening rows or contacting a provider."""
import argparse
from pathlib import Path
from cerebro.preflight import check_preflight
from cerebro.paths import ROOT,DEFAULT_BUNDLE

def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument('--csv-dir',type=Path,default=ROOT/'database')
    parser.add_argument('--manifest',type=Path)
    parser.add_argument('--database',type=Path,default=ROOT/'data'/'workshop.duckdb')
    parser.add_argument('--materialization-receipt',type=Path)
    parser.add_argument('--provider-capability-receipt',type=Path)
    args=parser.parse_args(argv)
    report=check_preflight(csv_dir=args.csv_dir,manifest_path=args.manifest,bundle_path=DEFAULT_BUNDLE,database_path=args.database,materialization_receipt_path=args.materialization_receipt,provider_capability_receipt_path=args.provider_capability_receipt)
    print(report.model_dump_json(indent=2))
    return 0 if report.live_prerequisites_ready else 2

if __name__=='__main__': raise SystemExit(main())
