"""Safe software-only entry point for Landy Heater.

This entry point deliberately imports no protocol module, constructs no UART
and sends no heater command.  Board verification is performed only through
separately invoked bring-up tools.
"""


def main():
    print("Landy Heater safe boot; UART inactive; protocol TX disabled")


if __name__ == "__main__":
    main()
