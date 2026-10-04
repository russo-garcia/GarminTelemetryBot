"""Explicit historical bootstrap/resume or bounded repair; never includes today."""
import argparse
from health_jobs import run_range, cli_result, add_bounds
from health_state import DEFAULT_HISTORY_START


def run_health_backfill(start, end, *, repair=False, **options):
    return run_range(start=start, end=end, repair=repair, **options)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Health bootstrap/resume; suggested first start: ' + DEFAULT_HISTORY_START)
    parser.add_argument('--start', required=True, help='YYYY-MM-DD; first bootstrap establishes history_start')
    parser.add_argument('--end', required=True, help='YYYY-MM-DD strictly before Berlin today')
    parser.add_argument('--repair', action='store_true', help='Refresh only already-finalized dates; never move the watermark')
    add_bounds(parser)
    args = vars(parser.parse_args(argv))
    return cli_result(lambda: run_health_backfill(**args))


if __name__ == '__main__':
    raise SystemExit(main())
