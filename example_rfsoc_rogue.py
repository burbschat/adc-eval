import pyrogue as pr
import numpy as np
import sys
import time
import spectrum
import matplotlib.pyplot as plt
import json
from pprint import pprint
import os
import pyvisa

# Note: This script is application specific for evaluation of RFSoC ADCs with
# custom firmware/software running (which is not included here).
# The signal generator used is an Agilent N5181B connected to the local
# network. Unfortionately it is a little broken and sometimes throws errors so
# functions are implemented to flush/handel those.

# Rogue IP/port
server_ip, server_port = sys.argv[1].split(":")
server_port = int(server_port)

# Signal generator IP
sg_ip = sys.argv[2]
sg_address = f"TCPIP0::{sg_ip}::inst0::INSTR"
rm = pyvisa.ResourceManager()

# Sample frequency of the ADC. This must be correct and is used to dermine the
# optimal buffer length. It is further assumed that the SG and ADC sampling
# clocks are locked!
ADC_SR = 509e6 * 8  # Depends on PLL config!
# Requested number of measurements over the available range
N_MEAS = 5

# Used channel (0 ~ 3)
CHANNEL = 1

# Set to dump data to json in the end
dump = False
data_dir = "./data/"


def error_reported(sg):
    error = sg.query("SYST:ERR?").strip()
    if error != '+0,"No error"':
        print(error)
        return True
    else:
        return False


def flush_errors(sg):
    n_errors = 0
    while error_reported(sg):
        n_errors += 1
        time.sleep(0.1)
    return n_errors


def set_frequency(f, sg):
    f_ghz = f * 1e-9
    # For some reason this sometimes fails. Retry until no errors are left.
    max_attempts = 10
    for attempt in range(max_attempts):
        sg.write(f"FREQ:CW {f_ghz} GHz")
        if flush_errors(sg) <= 0:
            # If no error reported we are done
            break
        else:
            print("Error encounterd, toggle rf output and retry frequency set...")
            sg.write("OUTP OFF")
            time.sleep(1)
            sg.write("OUTP ON")
    if attempt >= max_attempts - 1:
        print(f"Exceeded maximum number of attempts ({attempt}), aborting...")
        exit()


def get_data(root):
    sig_sampled = root.AmpDispProcessor[CHANNEL].WaveformData.get()
    return sig_sampled


def largest_rational_divisor(n, k):
    for m in range(k, 0, -1):
        if n % m == 0:
            return m
    return None  # No such m found


# Create a Virtual Client to connect to the Virtual Server via Zeromq
with pr.interfaces.VirtualClient(addr=server_ip, port=server_port) as client, rm.open_resource(sg_address) as sg:
    # Pointer to client root
    root = client.Root

    # Determine buffer length
    sig_sampled = get_data(root)
    buffer_len = len(sig_sampled)
    # Find optimal buffer length for truncation to run without windowing
    buffer_len_trunc = largest_rational_divisor(ADC_SR, buffer_len)
    # Corresponding SG frequency increment
    f_sg_inc = ADC_SR / buffer_len_trunc

    # Frequencies to run (Hz)
    frequencies_sg = np.arange(0, 2e9, f_sg_inc)  # All possible
    avail_freq_step_size = len(frequencies_sg) // (N_MEAS)
    # Reduce to number requested (not exact though), make sure to remove 0 Hz
    # but hit the maximum (or close to it).
    frequencies_sg = frequencies_sg[::avail_freq_step_size] + avail_freq_step_size * f_sg_inc

    # Set ouput power
    sg.write("POW:AMPL -5")  # in dBm
    # Make sure RF output is on
    sg.write("OUTP ON")

    for f_sg in frequencies_sg:
        # Set the signal generators frequency (output power is assumed to be already set for now)
        # sg.write(f"FREQ:CW {f_sg} GHz")
        set_frequency(f_sg, sg)
        # Wait a little until data ready
        time.sleep(2)

        # Software trigger, then get data
        # root.RFSoC.Application.ReadoutCtrl.SwFaultTrig()
        # time.sleep(1)
        # sig_sampled = root.AmpFaultProcessor.WaveformData[channel].get()

        # Get data for periodically triggered buffer
        sig_sampled = get_data(root)
        sig_sampled = sig_sampled[:buffer_len_trunc]  # Truncate to optimal length
        sig_sampled = sig_sampled - min(sig_sampled)

        # ADC parameters
        adc_freq = ADC_SR  # Sample rate, depends on PLL config!
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

        # Analyze spectrum
        # TODO: For some reason with unform window worse ENOB observed. This
        # could mean that we are overcorrecting for the windowing effect?
        # Perhaps also the interleaving spurs have larger effect with a uniform
        # window, as for say the hanning window they appear smaller, but we
        # only apply the window correction to the fundamental peak and its
        # harmonics when measuring their power. Thus for a uniform window the
        # large spurs may have a larger effect? -> Cutting out the bins does
        # not make a significant difference, so no.
        vars_dict = spectrum.analyze(sig_sampled, adc_bits, adc_vref, adc_freq, window="uniform", sp_leak=2)
        # vars_dict = spectrum.analyze(sig_sampled, adc_bits, adc_vref, adc_freq, window="hanning", sp_leak=10)

        # Show plots
        spectrum.plot(vars_dict)

        # Add some further metadata to vars_dict
        vars_dict["adc_channel"] = CHANNEL
        vars_dict["timestamp"] = time.time()
        vars_dict["f_sg"] = f_sg

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

        # Dump data
        if dump:
            json_file_name = str(vars_dict["timestamp"]).replace(".", "_") + ".json"
            json_file_path = os.path.join(data_dir, json_file_name)
            with open(json_file_path, "w") as f:
                json.dump(vars_dict, f, indent=4)

    # Turn off RF output
    sg.write("OUTP OFF")
