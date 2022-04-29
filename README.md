# pyTektronix

Python interface to communicate with the "Tektronix" oscilloscope.

## Usage:
    osc = Oscilloscope("192.168.3.83", use_serial=True, print_idn=True)
    data = osc.get_data(["CH2", "CH1"])

    print(data.sources)
    print(data["CH1"])
    print(data.header())

# Copyrights
All rights reserved &copy; 2022