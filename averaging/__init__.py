"""One position from a whole recording.

Reads a CSV this application wrote, and turns every epoch in it into a
single averaged position with an estimate of how well that position is
known. Qt-free, like :mod:`greis` and :mod:`recording`, so the arithmetic
is tested without a window.
"""
