"""CLI entry point for the daily metric collector.

Usage:
    python -m src.performance_engine.cli --profile-slug juanc-18259777

For cron:
    0 9 * * * cd /path/to/sales-agent && python -m src.performance_engine.cli --profile-slug juanc-18259777
"""

import argparse
import logging

from .collector import collect_wallapop_metrics


def main():
    parser = argparse.ArgumentParser(description="Collect daily listing metrics from Wallapop")
    parser.add_argument("--profile-slug", required=True,
                        help="Wallapop profile slug (from profile URL, e.g. 'juanc-18259777')")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    metrics = collect_wallapop_metrics(profile_slug=args.profile_slug)

    print(f"Collected metrics for {len(metrics)} listings:")
    for m in metrics:
        print(f"  {m.item_id}: views={m.views}, favs={m.favourites}")


if __name__ == "__main__":
    main()
