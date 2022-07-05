import anvil.pico
import uasyncio as a
from machine import Pin

# This is an example Anvil Uplink script for the Pico W.
# See https://anvil.works/pico for more information

UPLINK_KEY = "<uplink_key_goes_here>"

# We use the LED to indicate server calls and responses.
led = Pin("LED", Pin.OUT, value=1)


# Call this function from your Anvil app:
#
#    anvil.server.call('pico_fn', 42)
#

@anvil.pico.callable(is_async=True)
async def pico_fn(n):
    # Output will go to the Pico W serial port
    print(f"Called local function with argument: {n}")

    # Blink the LED and then double the argument and return it.
    for i in range(10):
        led.toggle()
        await a.sleep_ms(50)
    return n * 2

# Connect the Anvil Uplink. In MicroPython, this call will block forever.

anvil.pico.connect(UPLINK_KEY)


# There's lots more you can do with Anvil on your Pico W.
#
# See https://anvil.works/pico for more information


