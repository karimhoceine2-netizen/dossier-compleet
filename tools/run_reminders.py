"""Send the reminders that are due (day 3, 7 and 14 after the request) and flag clients to call.

Run daily (cron/launchd in a real setup). In the demo, --dagen first moves the demo clock forward.

    python3 tools/run_reminders.py [--dagen 3]
"""

import argparse

import dossier_db as dbx


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--dagen", type=int, default=0, help="demo: move the clock this many days forward first")
    p.add_argument("--base-url", default="http://localhost:8787")
    a = p.parse_args()
    actions = dbx.advance_clock(a.dagen, a.base_url) if a.dagen else dbx.run_reminders(a.base_url)
    print(f"Datum: {dbx.now():%d-%m-%Y}")
    print("\n".join(actions) or "Geen herinneringen nodig.")


if __name__ == "__main__":
    main()
