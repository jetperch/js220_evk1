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

"""Control an RGB LED.

This library uses a 32-bit integer to represent colors with
4 8-bit (byte) channels: red, green, blue, alpha.
The channels are packed in little-endian format:
    31:24  alpha (ignored)
    23:16  blue
    15:8   green
    7:0    red
"""

import math
from machine import Timer


def _rgba_blend(v1, v2, f):
    c = 0
    for i in range(4):
        shift = 8 * i
        x1 = (v1 >> shift) & 0xff
        x2 = (v2 >> shift) & 0xff
        y = ((x1 * (0xffff - f)) + x2 * f) >> 16  # linear blend
        c |= (y & 0xff) << shift
    return c


_hsv_lookup = {
    0: (3, 2, 0),
    1: (1, 3, 0),
    2: (0, 3, 2),
    3: (0, 1, 3),
    4: (2, 0, 3),
    5: (3, 0, 1),
}


_trans_pride_white = 0x00f0_d0ff
_trans_pride_blue = 0x00f0_a055
_trans_pride_pink = 0x0098_70ff  # 0x009966ff
TRANS_PRIDE_COLORS = [_trans_pride_blue, _trans_pride_pink, _trans_pride_white,
                      _trans_pride_pink, _trans_pride_blue]


def _u8_saturate(v):
    return min(255, max(0, int(v)))


def hsv_u8_to_rgb_u32(h, s, v):
    """Convert HSV to RGB.

    :param h: The hue from 0 to 255.
    :param s: The saturation from 0 to 255.
    :param v: The value from 0 to 255.
    :return: The u32 RGB value.

    http://www.easyrgb.com/index.php?X=MATH&H=21#text21
    """
    h = (h & 0xff) / 256.0
    s = _u8_saturate(s) / 255.0
    v = _u8_saturate(v)
    if s == 0:
        return v | (v << 8) | (v << 16)
    h *= 6.0
    i = int(math.floor(h))
    f = h - i  # fractional part of h
    x = int(v * (1.0 - s))
    y = int(v * (1.0 - s * f))
    z = int(v * (1.0 - s * (1.0 - f)))
    w = v
    colors = [x, y, z, w, 0]
    k = _hsv_lookup.get(i, (4, 4, 4))
    r, g, b = colors[k[0]], colors[k[1]], colors[k[2]]
    return r | (g << 8) | (b << 16)


def color_sequence_extend(colors, n=None):
    """Extend the color sequence to stay at each color longer betweed fades.

    :param colors: The list of colors.
    :param n: The number of times to repeat each color.
        None (default) is 2.
    """
    if n is None:
        n = 2
    else:
        n = int(n)
    assert(n >= 1)

    result = []
    for c in colors:
        for _ in range(n):
            result.append(c)
    return result


class RgbLedController:
    """Control an RGB LED to produce lighting effects.

    :param cbk_rgb_u32: The callable(u32) to set the LED.  The u32
        value is 23:16 blue, 15:8 green, 7:0 red.

    The controller can set fixed LED values or produce animations
    that cycle through colors.

    To set colors directly:
        led.u32 = 0x0000FF  # red
        led.u32 = 0x00FF00  # green
        led.u32 = 0xFF0000  # blue
        led.u32 = 0xFF00FF  # magenta

    Or using u8:
        led.u8(0xff, 0x00, 0xff)  # magenta
        led.u8(red=0xff, green=0x00, blue=0xff)  # magenta

    To only change the green intensity:
        led.u8(green=128)

    To blink between red, white, and blue with the default period:
        led.animate_blink([0x0000_00FF, 0x00FF_FFFF, 0x00FF_0000])

    To blink between red, white, and blue with a 1 second period:
        led.animate_blink([0x0000_00FF, 0x00FF_FFFF, 0x00FF_0000], period_ms=1000)

    To fade between red, white, and blue:
        led.animate_fade([0x0000_00FF, 0x00FF_FFFF, 0x00FF_0000])

    To sequence with less fade duration and more pausing at each color:
        colors = color_sequence_extend([0x0000_00FF, 0x00FF_FFFF, 0x00FF_0000], n=3)
        led.animate_fade(colors, period_ms=3000)

    To display a rainbow with the default period:
        led.animate_rainbow()

    To display the trans pride flag colors:
        led.animate_trans_pride()

    To stop all animations and keep the current color:
        led.stop()

    To stop all animations and turn the LED off:
        led.off()
    """

    def __init__(self, cbk_rgb_u32):
        self._cbk_rgb_u32 = cbk_rgb_u32
        self._counter = 0
        self._velocity = 0
        self._saturation = 0
        self._value = 0
        self._colors = None
        self._color_now = 0
        self._timer = Timer()
        self._period_ms = 10  # 100 Hz

    @property
    def u32(self):
        """Get the present LED intensity."""
        return self._color_now

    @u32.setter
    def u32(self, value):
        """Directly set the LED intensity.

        If an animation is running, the sequence will
        quickly overwrite any manually set value.  You will need
        to :meth:`stop` first.
        """
        self._color_now = value
        self._cbk_rgb_u32(value)

    def u8(self, red=None, green=None, blue=None):
        """Set the LED color given independent u8 values.

        :param red: The red intensity from 0 to 255.
        :param green: The green intensity from 0 to 255.
        :param blue: The blue intensity from 0 to 255.

        A default None value keeps the existing intensity of that color.
        """
        u32 = self.u32
        if red is not None:
            r = _u8_saturate(red)
            u32 = (u32 & 0xffff_ff00) | r
        if green is not None:
            g = _u8_saturate(green)
            u32 = (u32 & 0xffff_00ff) | (g << 8)
        if blue is not None:
            b = _u8_saturate(blue)
            u32 = (u32 & 0xff00_ffff) | (b << 16)
        self.u32 = u32

    def animate_blink(self, colors, period_ms=None):
        """Start an animation for a exact color sequence.

        :param colors: The list of color values as u32.
        :param period_ms: The time to cycle through all colors in milliseconds.
        """
        if not len(colors):
            self.stop()
            return
        if period_ms is None:
            period_ms = 2000
        self._colors = colors
        self._value = 0
        period = period_ms // len(colors)
        self.u32 = colors[0]
        self._timer.init(period=period, callback=self._process_blink)

    def animate_fade(self, colors, period_ms=None):
        """Start an animation for an smoothly fading color sequence.

        :param colors: The list of color values as u32.
        :param period_ms: The time to cycle through all colors in milliseconds.
            None (default) is 2000 (2 seconds).
        """
        if not len(colors):
            self.stop()
            return
        if period_ms is None:
            period_ms = 2000
        self._colors = colors
        self._value = 0
        self._counter = 0
        self._velocity = int((0xffff * self._period_ms * len(colors)) // period_ms)
        print(f'velocity = {self._velocity}, colors = {self._colors}')
        self._timer.init(period=self._period_ms, callback=self._process_fade)

    def animate_rainbow(self, hue=None, saturation=None, value=None, period_ms=None):
        """Start an HSV rainbow.

        :param hue: The initial u8 hue.
        :param saturation: The u8 staturation.
        :param value: The u8 value.
        :param period_ms: The period of an entire rainbow in milliseconds.
        """
        if period_ms is None:
            period_ms = 2000
        self._saturation = 255 if saturation is None else int(saturation)
        self._value = 255 if value is None else int(value)
        self._counter = 0
        self._velocity = int(0xffff * self._period_ms) // period_ms
        self._timer.init(period=self._period_ms, callback=self._process_rainbow)

    def animate_trans_pride(self, period_ms=None):
        """Start an color sequence displaying the trans pride flag colors.

        :param period_ms: The entire sequence period in milliseconds.
        """
        colors = color_sequence_extend(TRANS_PRIDE_COLORS)
        return self.animate_fade(colors, period_ms)

    def stop(self):
        """Stop any running animation.

        This method preserves the current color.  See :meth:`off`.
        """
        self._timer.deinit()

    def off(self):
        """Stop any running animation and turn the LED off."""
        self.stop()
        self.u32 = 0

    def _process_rainbow(self, timer):
        self._counter = (self._counter + self._velocity) & 0xffff
        hue = self._counter >> 8
        self.u32 = hsv_u8_to_rgb_u32(hue, self._saturation, self._value)

    def _process_blink(self, timer):
        self._value += 1
        if self._value >= len(self._colors):
            self._value = 0
        self.u32 = self._colors[self._value]

    def _process_fade(self, timer):
        self._counter = int(self._velocity + self._counter)
        if self._counter > 0xffff:
            self._value += self._counter >> 16
            self._counter &= 0xffff
            while self._value >= len(self._colors):
                self._value -= len(self._colors)
        if self._value + 1 == len(self._colors):
            v1, v2 = self._colors[-1], self._colors[0]
        else:
            v1, v2 = self._colors[self._value], self._colors[self._value + 1]
        v = _rgba_blend(v1, v2, self._counter)
        self.u32 = v
