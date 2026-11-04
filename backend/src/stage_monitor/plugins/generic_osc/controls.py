import math

from .protocol import encode


def validate_control(control):
    kind = control.get('kind', 'trigger')
    encode(control['address'], control.get('arguments', []))
    if kind == 'momentary':
        encode(control.get('release_address') or control['address'], control['release_arguments'])
    if kind == 'toggle':
        encode(control.get('off_address') or control['address'], control['off_arguments'])
        if control.get('feedback_on', True) == control.get('feedback_off', False):
            raise ValueError('Toggle feedback values must differ')
    if kind == 'slider':
        low, high, step = control['minimum'], control['maximum'], control['step']
        if not all(type(v) in (int, float) and math.isfinite(v) for v in (low, high, step)) or not low < high or not 0 < step <= high-low:
            raise ValueError('Invalid slider range or step')
        index = control.get('value_index', 0)
        args = control['arguments']
        if index >= len(args) or args[index]['type'] not in {'i', 'f'}:
            raise ValueError('Slider needs an i/f argument at value_index')
        if args[index]['type'] == 'i' and any(v != int(v) for v in (low, high, step)):
            raise ValueError('Integer sliders require integer range and step')
        if args[index]['type'] == 'i' and not -(2**31) <= low <= high < 2**31:
            raise ValueError('Integer slider range exceeds int32')
        if args[index]['type'] == 'f' and max(abs(low), abs(high)) > 3.4028234e38:
            raise ValueError('Float slider range exceeds float32')


def arguments_for(control, action, value=None):
    kind = control.get('kind', 'trigger')
    allowed = {'trigger': {'trigger'}, 'momentary': {'press', 'release'},
               'toggle': {'on', 'off'}, 'slider': {'set'}}[kind]
    if action not in allowed:
        raise ValueError('Action does not match OSC control')
    args = [dict(argument) for argument in control.get('arguments', [])]
    if action == 'release':
        args = control['release_arguments']
    if action == 'off':
        args = control['off_arguments']
    if kind == 'slider':
        if type(value) not in (float, int) or not math.isfinite(value) or not control['minimum'] <= value <= control['maximum']:
            raise ValueError('OSC slider value outside range')
        minimum, step = control['minimum'], control['step']
        value = min(control['maximum'], max(minimum, minimum+round((value-minimum)/step)*step))
        index = control.get('value_index', 0)
        args[index]['value'] = int(value) if args[index]['type'] == 'i' else value
    return args
