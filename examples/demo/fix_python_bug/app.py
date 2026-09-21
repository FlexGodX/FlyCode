#!/usr/bin/env python3
"""Line counter with bugs.

BUG: `count += line` adds strings together instead of counting them.
BUG: no handling for a missing file.
"""

import sys


def count_lines(filename):
    count = 0
    with open(filename) as f:
        for line in f:
            count += line
    return count


if __name__ == "__main__":
    path = sys.argv[1]
    print(f"Lines in {path}: {count_lines(path)}")
