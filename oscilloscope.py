from io import StringIO
import pyvisa as visa
import numpy as np
import requests

FORMATTER_LOOKUP = {
    "1": {"RI": "b", "RP": "B"},
    "2": {"RI": "h", "RP": "H"},
    "4": {"RI": "i", "RP": "I", "FP": "f"},
    "8": {"RI": "q", "RP": "Q", "FP": "d"},
}


class Oscilloscope:
    """
    Reads signal from the oscilloscope and returns a numpy array for each channel

    Usage:
        osc = Oscilloscope("192.168.3.83", use_serial=False, print_idn=False)
        data = osc.get_data(["CH2", "CH1"])

        print(data.sources)
        print(data["CH1"])
        print(data.header())
        
    """

    def __init__(self, ip: str = "", use_serial: bool = True, print_idn: bool = False):
        """
        Reads signal from the oscilloscope and returns a numpy array for each channel
        :param ip: IP address for the instrument
        :param use_serial: Use VISA connection, If False, uses HTTP
        :param print_idn: Printing identification is only for VISA connection (use_serial should be True)

        """
        self.use_serial = use_serial
        self.scope = None
        if ip:
            self.ip = ip
            if use_serial:
                self.rm = visa.ResourceManager()
                self.connect(ip)
                if print_idn:
                    print(self.scope.query("*IDN?"))

    def connect(self, ip):
        """
        Opens VISA connection to the provided IP address
        :param ip: IP address for the instrument
        :return:
        """
        self.scope = self.rm.open_resource('TCPIP::{}::INSTR'.format(ip), write_termination='\n', read_termination='\n')

    def set_timeout(self, seconds=5000):
        """
        Sets timeout for VISA connection
        :param seconds:
        :return:
        """
        self.scope.timeout = seconds

    def make_post(self, ch: str):
        """
        Creates POST data for the HTTP request
        :param ch:
        :return:
        """
        return {
            'WFMFILENAME': '{}'.format(ch.upper()),
            'WFMFILEEXT': 'csv',
            'command': 'select:control {}'.format(ch.lower()),
            'command1': 'save:waveform:fileformat spreadsheet',
            'wfmsend': 'Get'
        }

    def make_request(self, data):
        """
        Sends the POST request to the instrument and returns the response body
        :param data: POST data as dict
        :return:
        """
        osc_r = requests.post('http://{}:80/data/mdo_data4.html'.format(self.ip), data)
        osc_r.raise_for_status()

        if osc_r.status_code == 200 and osc_r.reason == 'OK':
            return osc_r.text
        else:
            print("Error contacting the device.")

    def parse_response(self, data_string):
        """
        Parses the response body of the HTTP request
        :param data_string:
        :return: Returns the channel data as WaveformCollection object
        """
        data_string = StringIO(data_string)
        header = {}
        wf = WaveformCollection()

        for _ in range(21):
            line = data_string.readline()
            words = line.strip().split(",")
            if words[0] == "Label":
                break
            if len(words) == 2 and words[0]:
                header[words[0]] = words[1]
        header["label"] = data_string.readline().strip().split(",")
        wf["header"] = header
        values = np.genfromtxt(data_string, delimiter=",", dtype=None)

        for i, label in enumerate(header["label"], start=0):
            wf[label] = values[:, i].tolist()
        return wf

    def get_data_http(self, channels: list):
        """
        Requests the channel data via HTTP
        :param channels: Channels as a list
        :return: Returns the channel data as WaveformCollection object

        """
        data = None
        for ch in channels:
            if data is None:
                data = self.parse_response(self.make_request(self.make_post(ch)))
            else:
                data += self.parse_response(self.make_request(self.make_post(ch)))
        return data

    def _get_header(self, source):
        """Returns the header as a dictionary so you can see configuration details"""
        self.scope.write("data:source " + source)
        self.scope.write("verbose ON;header ON")
        result_string = self.scope.query("wfmoutpre?")
        self.scope.write("verbose OFF;header OFF")
        result_string = result_string.replace(":WFMOUTPRE:", "", 1)
        # split on semicolons and break those into key/value pairs by splitting on space
        return {x.split(" ")[0]: x.split(" ")[1] for x in result_string.split(";")}

    def _get_data_visa(self, sources, lower_bound=None, upper_bound=None):
        """queries data and returns it along with the corresponding header"""
        for source in sources:
            self.scope.write("data:source " + source)
            if lower_bound is None:
                self.scope.write("data:start 1")
            else:
                self.scope.write("data:start " + str(lower_bound))
            length = self.scope.query("horizontal:recordlength?")
            if upper_bound is None:
                self.scope.write("data:stop " + length)
            else:
                self.scope.write("data:stop " + str(upper_bound))

            header = self._get_header(source)
            if self.scope.query("select:" + source + "?")[0] == "0":
                # The channel we want to read is off. Just return empty data and the header
                return [], header
            y_mult = float(header["YMULT"])
            y_offset = float(header["YOFF"])
            y_zero = float(header["YZERO"])
            ret_val = []
            if header["ENCDG"] == "ASCII":
                read_string = self.scope.query("curve?")
                ret_val = [
                    ((float(entry) - y_offset) * y_mult) + y_zero
                    for entry in read_string.split(",")
                ]
            elif header["ENCDG"] == "BINARY":
                format_string = FORMATTER_LOOKUP[header["BYT_NR"]][header["BN_FMT"]]
                is_big_endian = header["BYT_OR"] == "MSB"
                ret_val = self.scope.query_binary_values(
                    "curv?", datatype=format_string, is_big_endian=is_big_endian
                )
                ret_val = [((entry - y_offset) * y_mult) + y_zero for entry in ret_val]
            yield (source, ret_val, header)

    def get_data_visa(self, channels: list):
        """
        Requests the channel data via VISA connection
        :param channels: Channels as list
        :return: Returns the channel data as WaveformCollection object
        """
        wf = WaveformCollection()
        wf.idn = self.scope.query("*IDN?")
        if channels:
            for ch_name, ch_data, ch_header in self._get_data_visa(channels):
                wf[ch_name] = ch_data
                wf["header"] = ch_header
        return wf

    def get_data(self, channels: list):
        """
        Requests the data from the instrument. VISA or HTTP protocol is used depending on the initialization
        :param channels: Channels as list
        :return: Returns the channel data as WaveformCollection object
        """
        if self.use_serial:
            return self.get_data_visa(channels)
        else:
            return self.get_data_http(channels)


class WaveformCollection:
    def __init__(self):
        self.idn = ""
        self._data = {}
        self._header = {}

    @property
    def sources(self):
        return list(self._data.keys())

    def header(self):
        return self._header

    def __getitem__(self, key):
        return self._data[key]

    def __setitem__(self, key, value):
        if key == "header":
            self._header = value
        else:
            self._data[key] = value

    def __len__(self):
        return len(self._data.keys())

    def __add__(self, other):
        if self.idn == other.idn:
            self._header.update(other.header())
            for name in other.sources:
                self[name] = other[name]
            return self
        else:
            raise AttributeError("Incompatible addition")
