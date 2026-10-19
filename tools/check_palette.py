"""Check palette.json for colour separation. Run after editing it.

    python3 tools/check_palette.py

Measures the smallest distance between any two colours in OKLab, under
normal vision and simulated protanopia, deuteranopia and tritanopia, plus
contrast against the page surface. Prints the offending pair so a bad
swap is easy to find.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _cvd as cvd

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SURFACES = {"light": "#fcfcfb", "dark": "#1a1a19"}


def check(path=os.path.join(HERE, "palette.json")):
    pal = json.load(open(path))
    floors = pal.get("_minimums", {})
    ok = True
    for mode, surface in SURFACES.items():
        hexes = pal[mode]
        worst = cvd.report(hexes, surface, mode)
        for kind, (dist, a, b) in worst.items():
            floor = floors.get("normal" if kind == "normal" else "cvd")
            # compare at the precision we print, so the verdict and the
            # number on screen never disagree
            if floor and round(dist, 1) < floor:
                ok = False
                print("    FAIL %s %s: %.1f below %.1f (%s vs %s)"
                      % (mode, kind, dist, floor, a, b))
        lo = min(cvd.contrast(cvd.hex_to_lin(h), cvd.hex_to_lin(surface)) for h in hexes)
        if floors.get("contrast") and round(lo, 2) < floors["contrast"]:
            ok = False
            print("    FAIL %s contrast %.2f below %.2f" % (mode, lo, floors["contrast"]))
    print("\n%s" % ("all floors met" if ok else "SOME FLOORS NOT MET"))
    return ok


if __name__ == "__main__":
    sys.exit(0 if check() else 1)
