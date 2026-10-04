"""Catch up the contiguous health watermark through Berlin yesterday only."""
import argparse
from health_jobs import run_range, cli_result, add_bounds


def run_daily(**options):
    return run_range(daily=True, **options)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Finalize missing past health days; explicit bootstrap must initialize state first')
    add_bounds(parser)
    args = vars(parser.parse_args(argv))
    return cli_result(lambda: run_daily(**args))


if __name__ == '__main__':
    raise SystemExit(main())
