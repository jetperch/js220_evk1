# Copyright 2022 Jetperch LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
JS220 Evaluation Kit 1 (EVK) MicroPython code.

MicroPython with the Raspberry PI RP2040 microcontroller has five different
ways to toggle signals over time:
1. GPIO set from REPL
2. GPIO set from for/while loop with time.sleep()
3. GPIO set from machine.Timer ISR
4. PWM
5. PIO

The PIO approach is the best for fast, controlled waveforms.  The EVK
provides a simple way to generate patterns.
"""

import micropython
import machine
from machine import Pin, PWM, mem32
from array import array
from rp2 import PIO, StateMachine, asm_pio
from uctypes import addressof
from ucollections import OrderedDict
from rp2040 import *
from rgb_led_controller import RgbLedController, color_sequence_extend

# https://docs.micropython.org/en/v1.10/reference/isr_rules.html
micropython.alloc_emergency_exception_buf(100)

PIO_BUF_SIZE = 1 << 16  # must be multiple of 4
assert (0 == (PIO_BUF_SIZE & 3))
pio_buf = bytearray(PIO_BUF_SIZE)
"""The singleton (but non-exclusive) sample buffer."""


class Pins:
    """Define the pins for the JS220 Evaluation Kit 1."""
    R_1 = 0
    R_10 = 1
    R_100 = 2
    R_1K = 3
    R_10K = 4
    R_100K = 5
    R_1M = 6
    R_10M = 7

    C_10U = 8
    C_PWM = 9
    EXT0 = 10
    EXT1 = 11
    EXT2 = 12

    GPIO13 = 13  # test point
    GPIO14 = 14  # test point
    GPIO15 = 15  # test point

    LED_BLUE = 16
    LED_RED = 17
    LED_GREEN = 18

    LDO_EN = 19
    BUCK_EN = 20

    VSET_BUCK_1V0 = 21
    VSET_BUCK_1V8 = 22
    VSET_BUCK_3V3 = 23

    UART_TX = 24
    UART_RX = 25

    VSET_LDO_1V8 = 26
    VSET_LDO_3V3 = 27
    GPIO28 = 28  # test point
    GPIO29 = 29  # not connected


_R_TO_MASK = {
    1: (1 << Pins.R_1),
    10: (1 << Pins.R_10),
    100: (1 << Pins.R_100),
    1_000: (1 << Pins.R_1K),
    10_000: (1 << Pins.R_10K),
    100_000: (1 << Pins.R_100K),
    1_000_000: (1 << Pins.R_1M),
    10_000_000: (1 << Pins.R_10M),
}


def r_to_mask(r):
    """Convert a desired resistance to the GPIO bitmap mask.

    :param r: The resistance in Ohms, which must exactly match
        on of the resistor values.
    :return: The integer bitmap mask.
    """
    return _R_TO_MASK[int(r)]


DEMO_SEQUENCES_BY_RESISTANCE = {
    # lists of [duration_clocks, resistance] entries
    'descend': [
        [1000, 1],
        [1000, 10],
        [1000, 1],
        [1000, 100],
        [1000, 10],
        [1000, 100],
        [1000, 10],
        [1000, 1_000],
        [1000, 100],
        [1000, 1_000],
        [1000, 100],
        [1000, 10_000],
        [1000, 1_000],
        [1000, 10_000],
        [1000, 1_000],
        [1000, 100_000],
        [1000, 10_000],
        [1000, 100_000],
        [1000, 10_000],
        [1000, 1_000_000],
        [1000, 100_000],
        [1000, 1_000_000],
        [1000, 100_000],
        [1000, 10_000_000],
        [1000, 1_000_000],
        [1000, 10_000_000],
        [1000, 1_000_000],
        [1000, 10_000_000],
    ],
}

DEMO_SEQUENCES_BY_MASK = {
    # lists of [duration_clocks, IO_mask] entries
    'wiggle': [
        [1000, 0x02],
        [100, 0x06],
        [1000, 0x02],
        [100, 0x06],
        [1000, 0x02],
        [100, 0x06],
        [1000, 0x02],
        [200, 0x06],
        [20, 0x0E],
        [200, 0x06],
        [20, 0x0E],
        [200, 0x06],
        [20, 0x0E],
        [200, 0x06],
        [20, 0x0E],
        [200, 0x06],
        [20, 0x0E],
        [200, 0x06],
        [5000, 0x40],
        [1000, 0x04],
        [100, 0x0C],
        [1000, 0x04],
        [100, 0x0C],
        [1000, 0x04],
        [100, 0x0C],
        [1000, 0x04],
        [200, 0x0C],
        [20, 0x1C],
        [200, 0x0C],
        [20, 0x1C],
        [200, 0x0C],
        [20, 0x1C],
        [200, 0x0C],
        [20, 0x1C],
        [200, 0x0C],
        [20, 0x1C],
        [200, 0x0C],
        [5000, 0x08],
        [5000, 0x10],
        [5000, 0x20],
        [5000, 0x40],
        [5000, 0x80],
    ],
}


def _u8_saturate(v):
    return min(255, max(0, int(v)))


def _u8_to_pwm(v):
    v = _u8_saturate(v)
    v += 1
    return 0xffff - ((v * v) - 1)


@asm_pio(out_init=(PIO.OUT_LOW, PIO.OUT_LOW, PIO.OUT_LOW, PIO.OUT_LOW,
                   PIO.OUT_LOW, PIO.OUT_LOW, PIO.OUT_LOW, PIO.OUT_LOW),
         out_shiftdir=PIO.SHIFT_RIGHT,
         autopull=True,
         pull_thresh=32,
         # fifo_join=PIO.JOIN_TX
         )
def _pio_program():
    out(pins, 8)  # set the output pins with the buffer data


class Js220Evk1:
    """The JS220 Evaluation Kit 1 class implementation.

    This class is normally used through a singleton "evk"
    instance created by the "boot.py" file.
    """

    def __init__(self):
        self._voltage = 0.0
        self._i_max = 0.0
        self.r_1 = Pin(Pins.R_1, Pin.OUT, value=0)
        self.r_10 = Pin(Pins.R_10, Pin.OUT, value=0)
        self.r_100 = Pin(Pins.R_100, Pin.OUT, value=0)
        self.r_1k = Pin(Pins.R_1K, Pin.OUT, value=0)
        self.r_10k = Pin(Pins.R_10K, Pin.OUT, value=0)
        self.r_100k = Pin(Pins.R_100K, Pin.OUT, value=0)
        self.r_1M = Pin(Pins.R_1M, Pin.OUT, value=0)
        self.r_10M = Pin(Pins.R_10M, Pin.OUT, value=0)
        self.c_10U = Pin(Pins.C_10U, Pin.OUT, value=0)
        self.c_pwm = PWM(Pin(Pins.C_PWM, Pin.OUT, value=0))

        self.ext0 = Pin(Pins.EXT0, Pin.IN, pull=None)
        self.ext1 = Pin(Pins.EXT1, Pin.IN, pull=None)
        self.ext2 = Pin(Pins.EXT2, Pin.IN, pull=None)

        self.ldo_en = Pin(Pins.LDO_EN, Pin.OUT, value=0)
        self.buck_en = Pin(Pins.BUCK_EN, Pin.OUT, value=0)

        self.vset_buck_1v0 = Pin(Pins.VSET_BUCK_1V0, Pin.IN, pull=None)  # drive to 0 to enable
        self.vset_buck_1v8 = Pin(Pins.VSET_BUCK_1V8, Pin.IN, pull=None)  # drive to 0 to enable
        self.vset_buck_3v3 = Pin(Pins.VSET_BUCK_3V3, Pin.IN, pull=None)  # drive to 0 to enable
        self.vset_ldo_1v8 = Pin(Pins.VSET_LDO_1V8, Pin.IN, pull=None)  # drive to 0 to enable
        self.vset_ldo_3v3 = Pin(Pins.VSET_LDO_3V3, Pin.IN, pull=None)  # drive to 0 to enable

        self.led_red = PWM(Pin(Pins.LED_RED))
        self.led_green = PWM(Pin(Pins.LED_GREEN))
        self.led_blue = PWM(Pin(Pins.LED_BLUE))
        for led_pwm in [self.led_red, self.led_green, self.led_blue]:
            led_pwm.duty_u16(0xffff)
            led_pwm.freq(machine.freq() // 0xFFFE)
        self.led = RgbLedController(self.led_rgb_u32)

        self._resistances = OrderedDict([
            # resistance Ω: [pin,        wattage (W)]
            (1.0, (self.r_1, 2.0)),
            (10.0, (self.r_10, 2.0)),
            (100.0, (self.r_100, 0.5)),
            (1000.0, (self.r_1k, 0.062)),
            (10_000.0, (self.r_10k, 0.062)),
            (100_000.0, (self.r_100k, 0.062)),
            (1_000_000.0, (self.r_1M, 0.062)),
            (10_000_000.0, (self.r_10M, 0.062)),
        ])
        self._ldo_voltages = {
            1.0: None,
            1.8: self.vset_ldo_1v8,
            3.3: self.vset_ldo_3v3,
        }
        self._buck_voltages = {
            0.6: None,
            1.0: self.vset_buck_1v0,
            1.8: self.vset_buck_1v8,
            3.3: self.vset_buck_3v3,
        }
        self.power_off()
        self.resistance = None

        self._pio_wr_addr = array('I', [0])  # length 1 array of u32
        self._sm = StateMachine(0, _pio_program, freq=machine.freq(), out_base=Pin(0))
        self.stop()

    def c_pwm_start(self, frequency=None, duty_u16=None):
        """Start the 1 µF PWM capacitor.

        :param frequency: The PWM frequency.  None (default) is 5 kHz.
        :param duty_u16: The duty cycle.  None (default) is 0x8000 (50%).
        :return: The actual PWM frequency.
        """
        frequency = 5000 if frequency is None else int(frequency)
        duty_u16 = 0x8000 if duty_u16 is None else int(duty_u16)
        f_min = (machine.freq() // 0xFFFE)
        f_max = machine.freq() // 2
        if frequency < f_min:
            raise RuntimeError(f'Frequency {frequency} to slow, f_min={f_min}')
        if frequency > f_max:
            raise RuntimeError(f'Frequency {frequency} to fast, f_max={f_max}')
        assert(f_min <= frequency <= f_max)
        assert(0 <= duty_u16 <= 0xffff)
        self.c_pwm.duty_u16(duty_u16)
        self.c_pwm.freq(frequency)

    def c_pwm_stop(self):
        """Stop the 1 µF PWM capacitor."""
        self.c_pwm.duty_u16(0)

    @property
    def voltage(self):
        """Get the presently configured output voltage."""
        return self._voltage

    @property
    def resistance(self):
        """Get the presently configured load resistance."""
        r_inv = 0.0
        for r, (pin, _) in self._resistances.items():
            if pin.value():
                r_inv = 1.0 / r
        if r_inv == 0.0:
            return 1e9
        else:
            return 1.0 / r_inv

    def _spec_check(self, voltage, i_max, resistance, max_w=None):
        if max_w is None:
            for r, (_, wattage) in self._resistances.items():
                max_w = wattage
                if resistance <= r:
                    break
        w = voltage * voltage / resistance
        if w > max_w:
            raise RuntimeError(f'Power rating exceeded at {w} W: {voltage} V, {resistance} Ω, {max_w} W max')
        i = voltage / resistance
        # print(f'i = {i}, i_max = {i_max}')
        if i > i_max:
            raise RuntimeError(f'Current rating exceeded at {i} A > {i_max} A limit')

    def resistance_mask_on(self, mask):
        """Enable the specified resistances by GPIO mask.

        :param mask: The mask for resistors to enable.
        :raise:
        """
        for r, (pin, wattage) in self._resistances.items():
            if mask & 1:
                self._spec_check(self._voltage, self._i_max, r, wattage)
                # print(f'Turn on {r} resistor');
                pin.on()
            mask = mask >> 1

    def resistance_mask_off(self, mask):
        for _, (pin, _) in self._resistances.items():
            if mask & 1:
                pin.off()
            mask = mask >> 1

    def resistance_on(self, r=None):
        """Enable the specified resistance(s).

        :param r: The resistance, which must be one of [1, 10, 100, 1000, 10_000, 100_000, 1_000_000, 10_000_000].
            None (default) does not change the resistance.

        This method does **not** affect the setting of any other resistor.
        """
        if r is None:
            return
        mask = r_to_mask(r)
        self.resistance_mask_on(mask)

    def resistance_off(self, r=None):
        """Disable the specified resistance.

        :param r: The resistance, which must be one of [1, 10, 100, 1000, 10_000, 100_000, 1_000_000, 10_000_000].
            None (default) disables all.

        This method does **not** affect the setting of any other resistor.
        """
        if r is None:
            mask = 0xffffffff
        else:
            mask = r_to_mask(r)
        self.resistance_mask_off(mask)

    @resistance.setter
    def resistance(self, r=None):
        """Configure the load to the specified resistance.

        :param r: The resistance, which must be one of [1, 10, 100, 1000, 10_000, 100_000, 1_000_000, 10_000_000].
        """
        if r is None:
            mask = 0
        else:
            mask = r_to_mask(r)
        self.resistance_mask_on(mask)    # make before
        self.resistance_mask_off(~mask)  # break

    def led_rgb_u32(self, color):
        """Set the LED color with a single packed u32 value.

        :param color: The 32-bit color in little endian format:
            * 31:24 = alpha (reserved), ignored for now
            * 23:16 = blue
            * 15:8 = green
            * 7:0 = red

        Use self.led rather than this function to control the LED.
        This function is used as the callback from self.led.
        """
        r = color & 0x0000ff
        g = (color & 0x00ff00) >> 8
        b = (color & 0xff0000) >> 16
        self.led_red.duty_u16(_u8_to_pwm(r))
        self.led_green.duty_u16(_u8_to_pwm(g))
        self.led_blue.duty_u16(_u8_to_pwm(b))

    def _power(self, values, voltage, i_max):
        self.power_off()
        try:
            p = values[voltage]
        except KeyError:
            raise ValueError(f'invalid voltage {voltage}.  Must be one of {values.keys()}')
        if p is not None:
            p.init(Pin.OUT, value=0)
        self._spec_check(voltage, i_max, self.resistance)
        self._voltage = voltage
        self._i_max = i_max

    def power_off(self):
        """Turn off the power."""
        self.ldo_en.off()
        self.buck_en.off()
        self._voltage = 0.0
        self._i_max = 0.0
        self.vset_buck_1v0.init(Pin.IN, pull=None)
        self.vset_buck_1v8.init(Pin.IN, pull=None)
        self.vset_buck_3v3.init(Pin.IN, pull=None)
        self.vset_ldo_1v8.init(Pin.IN, pull=None)
        self.vset_ldo_3v3.init(Pin.IN, pull=None)

    def power_buck(self, voltage):
        """Configure the buck power supply.

        :param voltage: The output voltage which must be on of [0.6, 1.0, 1.8, 3.3].
        """
        self._power(self._buck_voltages, voltage, 1.0)
        self.buck_en.on()

    def power_ldo(self, voltage):
        """Configure the LDO power supply.

        :param voltage: The output voltage which must be on of [1.0, 1.8, 3.3].
        """
        self._power(self._ldo_voltages, voltage, 0.15)
        self.ldo_en.on()

    def pio_start(self, buffer, samples, frequency):
        """Start the PIO program to output pio_buf.

        :param buffer: The buffer containing the data.  Normally,
            you should provide the global pio_buf.  This buffer
            must remain valid until :meth:`stop`.
        :param samples: The number of samples in the buffer.
            Must be betweeen 1 and 2**16.
            Must be a multiple of 4.
        :param frequency: The desired output frequency.
        :return: The actual output frequency.

        This function uses 2 DMA channels.
        Channel 0 transfers 65536 bytes and chains to channel 1.
        Channel 1 transfers the starting address to channel 0 to
        reset channel 0 and then chains to channel 0.

        While the DMAs do support ring bufffers, they only support
        up to 32768 bytes.  We want a larger buffer, so we use
        DMA chaining instead.
        """
        self.stop()
        if samples & 3:
            raise ValueError(f'Samples must be a multiple of 4: given {samples}')
        src = addressof(buffer)
        if src & 3:
            raise ValueError(f'Buffer address must start on word boundary: given {src}')

        # Drive resistance outputs from PIO0
        for pin, _ in self._resistances.values():
            pin.init(mode=Pin.ALT, alt=6)

        # Configure ch0 to transfer buffer to PIO0, chain to ch1
        mem32[DMA_CH0_READ_ADDR] = 0  # populated by ch1
        mem32[DMA_CH0_WRITE_ADDR] = PIO0_TXF0
        mem32[DMA_CH0_TRANS_COUNT] = samples >> 2
        mem32[DMA_CH0_AL1_CTRL] = (  # don't start yet
                DMA_CTRL_IRQ_QUIET |
                DMA_CTRL_TREQ_SEL_DREQ_PIO0_TX0 |
                DMA_CTRL_CHAIN_TO_CH1 |
                DMA_CTRL_INCR_READ |
                DMA_CTRL_DATA_SIZE_WORD |
                DMA_CTRL_HIGH_PRIORITY |
                DMA_CTRL_EN)

        # Configure ch1 to write channel ch0 read address, chain to ch0
        self._pio_wr_addr[0] = src
        mem32[DMA_CH1_READ_ADDR] = addressof(self._pio_wr_addr)
        mem32[DMA_CH1_WRITE_ADDR] = DMA_CH0_READ_ADDR
        mem32[DMA_CH1_TRANS_COUNT] = 1  # just the address
        mem32[DMA_CH1_CTRL_TRIG] = (
                DMA_CTRL_IRQ_QUIET |
                DMA_CTRL_TREQ_SEL_NONE |
                DMA_CTRL_CHAIN_TO_CH0 |
                DMA_CTRL_DATA_SIZE_WORD |
                DMA_CTRL_HIGH_PRIORITY |
                DMA_CTRL_EN)

        # Configure PIO frequency to have no fractional parts and start
        fs = machine.freq()
        frequency = min(frequency, fs)
        f_div = min(fs // frequency, 0xffff)
        mem32[PIO0_SM0_CLKDIV] = f_div << 16
        freq = fs // f_div
        self._sm.active(1)
        return freq

    def pio_start_sequence(self, frequency, sequence, use_mask=None):
        """Start the PIO program to output pio_buf.

        :param frequency: The desired output frequency.
        :param sequence: Either a sequence name or the
            list of entries [samples, value].
            use_mask determines whether value is a resistance or a mask.
            Sequence names include ['descend', 'wiggle'].
            The list of entries will be expanded into pio_buf.
            The actual sequence will be padded to a multiple of 4.
            This pattern will be interpreted to create a
            make-before-break sequence which effectively extends the
            pulse by 1 period.
        :return: The actual output frequency.
        :note: This function will populate pio_buf and overwrite any
            existing pattern.
        """
        length = 0
        mask = 0
        mask_prev = 0
        if isinstance(sequence, str):
            if sequence in DEMO_SEQUENCES_BY_RESISTANCE:
                sequence = DEMO_SEQUENCES_BY_RESISTANCE[sequence]
                use_mask = False
            elif sequence in DEMO_SEQUENCES_BY_MASK:
                sequence = DEMO_SEQUENCES_BY_MASK[sequence]
                use_mask = True
        for samples, value in sequence:
            if (length + samples) > len(pio_buf):
                raise ValueError(f'length {length} > {len(pio_buf)}')
            mask = value if use_mask else r_to_mask(value)
            pio_buf[length] = mask | mask_prev  # make-before-break
            for i in range(length + 1, length + samples):
                pio_buf[i] = mask
            mask_prev = mask
            length += samples
        while length & 3:
            pio_buf[i] = mask
            length += 1
        pio_buf[0] |= mask
        return self.pio_start(pio_buf, length, frequency)

    def pio_stop(self):
        """Stop the PIO load drive and return pins to direct software control."""
        self._sm.active(0)
        mem32[DMA_CHAN_ABORT] = 3
        while mem32[DMA_CHAN_ABORT]:
            pass  # poll until 0
        mem32[DMA_CH0_AL1_CTRL] = 0
        mem32[DMA_CH1_AL1_CTRL] = 0

        # Return pins to software control
        for pin, _ in self._resistances.values():
            pin.init(Pin.OUT, value=0)

    def stop(self):
        self.pio_stop()
        self.c_pwm.duty_u16(0)

    def off(self):
        """Disable both power and load."""
        self.power_off()
        self.stop()
        self.resistance_mask_off(0xff)
        self.c_10U.off()

    def current_range_tester(self, use_buck=False):
        """Drive the JS220 through all current ranges.

        :param use_buck: True to use buck converter as the power supply.
            False (default) to use the LDO as the power supply.  Since
            the LDO is limited to 150 mA output max, we need to skip the
            1 Ω resistor.

        Cycle the JS220 through all current ranges by driving 1V into
        different resistances.  Change resistance (and current range) every
        10 milliseconds.

        You can optionally set c_10U.on() before calling this method
        to enable the 10 uF load capacitance.
        """
        pio_fs = 10_000
        levels = 8
        samples_per_level = 1000
        pio_sz = samples_per_level * levels
        self.stop()
        if use_buck:
            self.power_buck(1.0)
            resistor_mask = 0xff
        else:
            self.power_ldo(1.0)
            resistor_mask = 0xfe  # 1 Ω resistor requires too much current for LDO
        for j in range(levels):
            k = j * samples_per_level
            mask = 0xff & (0xff << j)
            r = 0x80 | (resistor_mask & mask)
            for i in range(samples_per_level):
                pio_buf[k + i] = r
        freq = self.pio_start(pio_buf, pio_sz, pio_fs)
        print(f'current_range_tester(use_buck={use_buck}) @ {freq} Hz')

    def demo(self):
        """The default demo"""
        print('Starting JS220 Evaluation Kit 1 Demo')
        print('Use evk.stop() to exit the demo')
        self.stop()
        self.power_ldo(1.0)
        self.c_10U.on()  # enable 10 uF across load by default
        self.pio_start_sequence(10_000, 'wiggle')
