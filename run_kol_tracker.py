"""Compatibility entrypoint using the production tracker and its shared lock."""
import sys

from kol_tracker import main


if __name__ == '__main__':
    sys.argv.insert(1, 'daily')
    main()
