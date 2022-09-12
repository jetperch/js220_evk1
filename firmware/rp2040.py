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

import machine
import gc
from ubinascii import hexlify

# Define DMA registers
# See RP2040 datasheet section 2.5.7, DMA list of registers
DMA_BASE = 0x5000_0000
DMA_OFFSET_READ_ADDR   = 0x00
DMA_OFFSET_WRITE_ADDR  = 0x04
DMA_OFFSET_TRANS_COUNT = 0x08
DMA_OFFSET_CTRL_TRIG   = 0x0c  # CTRL and write triggers DMA start (RP2040 Section 2.5.2)
DMA_OFFSET_AL1_CTRL    = 0x10  # CTRL but does not trigger DMA start

DMA_CTRL_IRQ_QUIET              = 1 << 21
DMA_CTRL_TREQ_SEL_DREQ_PIO0_TX0 = 0 << 15
DMA_CTRL_TREQ_SEL_NONE          = 0x3f << 15
DMA_CTRL_CHAIN_TO_CH0           = 0 << 11
DMA_CTRL_CHAIN_TO_CH1           = 1 << 11
DMA_CTRL_INCR_WRITE             = 1 << 5
DMA_CTRL_INCR_READ              = 1 << 4
DMA_CTRL_DATA_SIZE_WORD         = 2 << 2
DMA_CTRL_HIGH_PRIORITY          = 1 << 1
DMA_CTRL_EN                     = 1

DMA_CH0_BASE        = DMA_BASE + 0x000
DMA_CH0_READ_ADDR   = DMA_CH0_BASE + DMA_OFFSET_READ_ADDR
DMA_CH0_WRITE_ADDR  = DMA_CH0_BASE + DMA_OFFSET_WRITE_ADDR
DMA_CH0_TRANS_COUNT = DMA_CH0_BASE + DMA_OFFSET_TRANS_COUNT
DMA_CH0_CTRL_TRIG   = DMA_CH0_BASE + DMA_OFFSET_CTRL_TRIG
DMA_CH0_AL1_CTRL    = DMA_CH0_BASE + DMA_OFFSET_AL1_CTRL

DMA_CH1_BASE        = DMA_BASE + 0x040
DMA_CH1_READ_ADDR   = DMA_CH1_BASE + DMA_OFFSET_READ_ADDR
DMA_CH1_WRITE_ADDR  = DMA_CH1_BASE + DMA_OFFSET_WRITE_ADDR
DMA_CH1_TRANS_COUNT = DMA_CH1_BASE + DMA_OFFSET_TRANS_COUNT
DMA_CH1_CTRL_TRIG   = DMA_CH1_BASE + DMA_OFFSET_CTRL_TRIG
DMA_CH1_AL1_CTRL    = DMA_CH1_BASE + DMA_OFFSET_AL1_CTRL

DMA_CHAN_ABORT      = DMA_BASE + 0x444

# Define PIO registers
# See RP2040 datasheet section 3.7, PIO list of registers
PIO0_BASE = 0x5020_0000
PIO1_BASE = 0x5030_0000
PIO0_CTRL = PIO0_BASE + 0x000
PIO0_FSTAT = PIO0_BASE + 0x004
PIO0_FDEBUG = PIO0_BASE + 0x008
PIO0_FLEVEL = PIO0_BASE + 0x00c
PIO0_TXF0 = PIO0_BASE + 0x010  # for DMA ch0 target address
PIO0_TXF1 = PIO0_BASE + 0x014
PIO0_TXF2 = PIO0_BASE + 0x018
PIO0_TXF3 = PIO0_BASE + 0x01c
PIO0_RXF0 = PIO0_BASE + 0x020
PIO0_RXF1 = PIO0_BASE + 0x024
PIO0_RXF2 = PIO0_BASE + 0x028
PIO0_RXF3 = PIO0_BASE + 0x02c
PIO0_IRQ = PIO0_BASE + 0x030
PIO0_IRQ_FORCE = PIO0_BASE + 0x034
PIO0_SM0_CLKDIV = PIO0_BASE + 0x0c8  # to override clock behavior
PIO0_SM0_EXECCTRL = PIO0_BASE + 0x0cc

# Define GPIO registers
# See RP2040 datasheet section 2.19.6
IO_BANK0_BASE   = 0x4001_4000
IO_GPIO0_STATUS = IO_BANK0_BASE + 0x000
IO_GPIO0_CTRL   = IO_BANK0_BASE + 0x004


def mem_info():
    gc.collect()
    ram_free = gc.mem_free()
    ram_alloc = gc.mem_alloc()
    ram_total = ram_alloc + ram_free
    ram_alloc_p = ram_alloc * 100 / ram_total
    ram_free_p = ram_free * 100 / ram_total
    print(f'RAM usage B: {ram_alloc} used, {ram_free} free, {ram_total} total')
    print(f'RAM usage %: {ram_alloc_p:.1f}% used, {ram_free_p:.1f}% free')


def info():
    """Display information about this EVK."""
    print(f'CPU Frequency: {machine.freq():,d} Hz')
    unique_id = hexlify(machine.unique_id()).decode('utf-8')
    print(f'Unique ID: {unique_id}')
    mem_info()
