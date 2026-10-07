import sys


def main() -> int:
    # Any arguments mean command line mode; none opens the window.
    if len(sys.argv) > 1:
        from .cli import main as cli_main

        return cli_main(sys.argv[1:])

    from .ui.application import FlatkeepApplication

    return FlatkeepApplication().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
