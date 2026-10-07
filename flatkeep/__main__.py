import sys


def main() -> int:
    """Run the command line if there are arguments, else open the window.
    Returns the exit status."""
    if len(sys.argv) > 1:
        from .cli import main as cli_main

        return cli_main(sys.argv[1:])

    from .ui.application import FlatkeepApplication

    return FlatkeepApplication().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
