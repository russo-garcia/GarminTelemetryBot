"""Retired legacy entry point. No credential reads, SDK imports or acquisition."""

class RenphoRetiredError(RuntimeError):
    pass


def get_renpho_metrics(date_str):
    raise RenphoRetiredError('Renpho acquisition is retired; use Garmin health collection')


def main():
    print('Renpho acquisition is retired. No provider operation was performed.')
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
