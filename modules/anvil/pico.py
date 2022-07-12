from async_websocket_client import AsyncWebsocketClient
from ca_certs import LETSENCRYPT_ROOT
import uasyncio as a
import time
import json
import random
import sys
from machine import Pin

__all__ = ['connect', 'connect_async', 'call', 'callable', 'callable_async', 'get_user_email']
__version__ = "0.1.1"
__author__ = "Ian Davies"

# Update this with each release.
NOT_BEFORE=1657267241

ws = AsyncWebsocketClient()
_stay_connected = True
    
async def _s(v):
    #print("SEND:", v)
    if isinstance(v, str):
        await ws.send(v)
    else:
        await ws.send(json.dumps(v))
    
async def _r():
    data = await ws.recv()
    if data and isinstance(data, str):
        #print("RECV:", data)
        return json.loads(data)
    
async def _register_callables():
    for fn_name, fn in fns.items():
        await _s({
            "type": "REGISTER",
            "name": fn_name,
        })

fns = {}
outstanding_calls = {}
call_stack_ids = {} # task -> call stack id

async def _incoming_call(data):
    try:
        f = fns[data.get('command')]
        if not f:
            raise Exception(f"No such function: {data.get('command')}")
        
        task_id = id(a.current_task())
        try:
            call_stack_ids[task_id] = data.get("call-stack-id")
            #print("New task ID:", id(a.current_task()))

            if f['require_user']:
                email = await get_user_email()
                #print("Got email", email)
                if not email:
                    raise Exception("Unauthorised. You must be logged in to call this function.")
                elif isinstance(f['require_user'], str) and email != f['require_user']:
                    raise Exception("Unauthorised. You are not authorised to call this function.")
                    
            res = f['fn'](*data.get('args'), **data.get('kwargs'))
            result = await res if f['is_async'] else res
        finally:
            del call_stack_ids[task_id]

        await _s({
            "id": data['id'],
            "response": result,
        })
    except Exception as e:
        sys.print_exception(e)
        await _s({
            "id": data['id'],
            "error": {'message' : str(e)},
        })
        
async def _anvil_listen():
    while await ws.open():
        data = await _r()
        if data:
            if data.get("objects"):
                await _s({
                    "id": data['id'],
                    "error": {'message' : f"Cannot send objects of type {[o['type'][0] for o in data['objects']]} to anvil.pico"},
                })
            elif "response" in data or "error" in data:
                if outstanding_calls.get(data['id']):
                    outstanding_calls[data['id']] = data
                else:
                    print(f"Received bogus response: {data}")
            elif "output" in data and data['output'].strip():
                print(f"Server: {data['output'].strip()}")
            elif data.get('type') == "CALL":
                #print("Call", data)
                a.create_task(_incoming_call(data))
                    
        else:
            a.sleep_ms(50)
            
RESPONSE_SENTINEL = object()

class FatalException(Exception):
    pass

async def _connect(key, url):
    if time.time() < NOT_BEFORE:
        raise FatalException("System time invalid. Not connecting to Anvil.")

    print("Connecting to Anvil...")
    await ws.handshake(url, ca_certs=LETSENCRYPT_ROOT)
    print("Connected")
    await ws.open()
    await _s({"key": key, "v": 7, "device": "PICO_W"})
    result = await _r()
    if not result.get("auth") == "OK":
        raise Exception("Connection failed: " + str(result))
    print(f"Authenticated to app {result['app-info']['id']}")

async def _launch_task(task, name):
    try:
        await task
    except Exception as e:
        print(f"Exception running uplink task: {name}")
        sys.print_exception(e)

async def _heartbeat():
    while _stay_connected:
        await a.sleep(10)
        await call("anvil.private.echo", "keep-alive")

async def _blink_led(led, interval, n=None):
    i = 0
    while _stay_connected:
        if n is not None:
            i += 1
            if i > n:
                break
        led.toggle()
        await a.sleep_ms(interval)
    led.on()

async def _connect_async(key, on_first_connect, on_every_connect, url, no_led):
    global _stay_connected
    _stay_connected = True
    if not no_led:
        led = Pin("LED", Pin.OUT, value=1)
    while _stay_connected:
        try:
            blink_task = None
            if not no_led:
                blink_task = a.create_task(_blink_led(led, 100))
            await _connect(key, url)
            await _register_callables()
            a.create_task(_heartbeat())
            if blink_task:
                blink_task.cancel()
                a.create_task(_blink_led(led, 50,10))
            if on_first_connect:
                a.create_task(_launch_task(on_first_connect, "on_first_connect"))
                on_first_connect = None
            if on_every_connect:
                a.create_task(_launch_task(on_every_connect, "on_every_connect"))
            await _anvil_listen()
        except FatalException as e:
            print("Fatal exception in uplink reconnection loop:")
            sys.print_exception(e)
            raise
        except Exception as e:
            print("Exception in uplink reconnection loop:")
            sys.print_exception(e)
        await a.sleep(1)

async def raise_event(name, payload=None, session_id=None, session_ids=None, channel=None):
    await anvil.pico.call("anvil.private.raise_event", name, payload, session_id=session_id, session_ids=session_ids, channel=channel)

async def get_user_email(allow_remembered=True):
    return await call("anvil.private.users.get_current_user_email", allow_remembered=allow_remembered)

def callable(name_or_fn=None, is_async=False, require_user=None):
    name = None
    def g(fn):
        fns[name or fn.__name__] = {"fn": fn, "is_async": is_async, "require_user": require_user}
        return fn

    if not name_or_fn or isinstance(name_or_fn, str):
        name = name_or_fn
        return g
    else:
        return g(name_or_fn)

def callable_async(*args, **kwargs):
    kwargs['is_async'] = True
    return callable(*args, **kwargs)

async def call(fn_name, *args, **kwargs):
    #print("Call in task", id(a.current_task()))
    req_id = f"pico-call-{time.time()}-{random.randint(1,99999)}"
    outstanding_calls[req_id] = RESPONSE_SENTINEL
    req = {
        "type": "CALL",
        "command": fn_name,
        "id": req_id,
        "args": args,
        "kwargs": kwargs,
    }
    call_stack_id = call_stack_ids.get(id(a.current_task()))
    if call_stack_id:
        req['call-stack-id'] = call_stack_id
    await _s(req)
    while outstanding_calls[req_id] is RESPONSE_SENTINEL:
        await a.sleep_ms(5)
    try:
        if 'response' in outstanding_calls[req_id]:
            return outstanding_calls[req_id]['response']
        else:
            raise Exception(outstanding_calls[req_id]['error']['message'])
    finally:
        del outstanding_calls[req_id]
    

def connect_async(key, on_first_connect=None, on_every_connect=None, url="wss://anvil.works/uplink", no_led=False):    
    return _connect_async(key, on_first_connect, on_every_connect, url, no_led)
        
def connect(key, on_first_connect=None, on_every_connect=None, url="wss://anvil.works/uplink", no_led=False):
    a.run(_connect_async(key, on_first_connect, on_every_connect, url, no_led))

async def disconnect():
    global _stay_connected
    _stay_connected = False
    await ws.close()
    print("Anvil uplink disconnected")
