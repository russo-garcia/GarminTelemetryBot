"""Legacy name for health backfill; now requires the same explicit bounded range.

This deprecated name now performs Garmin-only whole-health acquisition.
It never imports/calls the retired Renpho provider.
Retain the administrative entry point without an uncoordinated automatic loop.
"""
from health_backfill import run_health_backfill, main


def backfill_history(start, end, **options):
    return run_health_backfill(start, end, **options)


if __name__ == '__main__':
    raise SystemExit(main())
