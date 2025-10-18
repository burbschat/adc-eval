import pyrogue as pr
import numpy as np
import sys
import time
import spectrum
import matplotlib.pyplot as plt
import json
from pprint import pprint
import os

# Note: This script is application specific for evaluation of RFSoC ADCs with
# custom firmware/software running (which is not included here).

server_ip, server_port = sys.argv[1].split(":")
server_port = int(server_port)

# Create a Virtual Client to connect to the Virtual Server via Zeromq
with pr.interfaces.VirtualClient(addr=server_ip, port=server_port) as client:

    # Pointer to client root
    root = client.Root

    # Used channel (0 ~ 3)
    channel = 1

    # Software trigger, then get data
    # root.RFSoC.Application.ReadoutCtrl.SwFaultTrig()
    # time.sleep(1)
    # sig_sampled = root.AmpFaultProcessor.WaveformData[channel].get()

    # Get data for periodically triggered buffer
    sig_sampled = root.AmpDispProcessor[channel].WaveformData.get()
    sig_sampled = sig_sampled - min(sig_sampled)

    # ADC parameters
    # adc_freq = 508.876e6 * 8  # Sample rate, depends on PLL config!
    adc_freq = 509e6 * 8  # Sample rate, depends on PLL config!
    adc_buff_n = len(sig_sampled)  # Buffer length
    adc_bits = 16  # Number of bits
    adc_quants = 2**adc_bits
    # The data sheet says 1V full-scale input (differential, i.e. VPPD).
    # The actual VPP value of the inserted signal will be probably somewhat higher due to attenuation in baluns?
    # https://docs.amd.com/r/en-US/ds926-zynq-ultrascale-plus-rfsoc/RF-ADC-Electrical-Characteristics
    adc_vref = 1.0
    adc_quant_v = adc_vref / adc_quants

    # Add a sine wave for testing
    # test_sine = 2**13 * np.sin(2 * np.pi * 100e6 * np.linspace(0, adc_buff_n * 1 / adc_freq, adc_buff_n))
    # sig_sampled = sig_sampled + test_sine

    # Analyze spectrum and show plots
    vars_dict = spectrum.analyze(sig_sampled, adc_bits, adc_vref, adc_freq, window="hanning")

    # Add some further metadata to vars_dict
    vars_dict["adc_channel"] = channel
    vars_dict["timestamp"] = time.time()

    def make_ndarray_serializable(obj):
        if isinstance(obj, dict):
            return {key: make_ndarray_serializable(value) for key, value in obj.items()}
        elif isinstance(obj, list):
            return [make_ndarray_serializable(item) for item in obj]
        elif isinstance(obj, tuple):
            return tuple(make_ndarray_serializable(item) for item in obj)
        elif isinstance(obj, np.ndarray):
            return obj.tolist()
        else:
            return obj

    vars_dict = make_ndarray_serializable(vars_dict)

    data_dir = "./data/"

    # Dump data
    json_file_name = str(vars_dict["timestamp"]).replace(".", "_") + ".json"
    json_file_path = os.path.join(data_dir, json_file_name)
    with open(json_file_path, "w") as f:
        json.dump(vars_dict, f, indent=4)
