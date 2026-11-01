"""Run the tests without pytest.

    python3 tests/run.py          # everything that works offline
    python3 tests/run.py --api    # also the tests that need the network

pytest works too and is better for running one test at a time. This
exists so the suite still runs on a machine with nothing installed,
which is the same reason the app itself is stdlib-only.
"""
import importlib
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.dirname(HERE), HERE]

OFFLINE = ("test_scoring", "test_standings", "test_robot", "test_insights",
           "test_assets", "test_transfers")
NETWORK = ("test_api",)


def main(argv):
    names = list(OFFLINE) + (list(NETWORK) if "--api" in argv else [])
    passed, failed = 0, []
    for name in names:
        module = importlib.import_module(name)
        for test in sorted(t for t in dir(module) if t.startswith("test_")):
            try:
                getattr(module, test)()
                passed += 1
            except Exception:
                failed.append("%s.%s" % (name, test))
                print("FAIL %s.%s" % (name, test))
                traceback.print_exc()
    print("\n%d passed, %d failed%s"
          % (passed, len(failed), "" if not failed else ": " + ", ".join(failed)))
    if "--api" not in argv:
        print("(%s skipped -- needs the network; add --api)" % ", ".join(NETWORK))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
