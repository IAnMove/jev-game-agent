"""Observed pipe transitions; route position follows the restored history, not X."""


def pipe_destination(state):
    ram = state.get('ram', {})
    if ram.get('routine', {}).get('value') not in (2, 3):
        return None
    if 'entrance_page' not in ram or 'area_pointer' not in ram:
        return None
    pointer = ram['area_pointer']['value']
    page = ram['entrance_page']['value']
    return {'key': f'{(pointer >> 5) & 3}:{pointer & 31}:{page}',
            'area_type': (pointer >> 5) & 3, 'area_index': pointer & 31, 'entry_page': page}


def confirmed_arrival(destination, after):
    return bool(destination and after.get('routine') == 8
                and after.get('area_type') == destination['area_type']
                and after['x']//256 in (destination['entry_page'], destination['entry_page']+1))


def navigation_context(history, current, guide):
    level = current.get('level')
    config = (guide or {}).get('levels', {}).get(level, {})
    room = config.get('initial_room', 'unknown')
    crossings = []
    for step in history:
        if step.get('to', {}).get('level') != level:
            room, crossings = config.get('initial_room', 'unknown'), []
            continue
        event = step.get('pipe_transition')
        if event and event.get('confirmed'):
            room = event['destination']['key']
            crossings.append(event)
    return {'current_room': room, 'room_description': config.get('rooms', {}).get(room),
            'confirmed_pipe_history': crossings[-16:], 'total_crossings_on_current_route': len(crossings),
            'meaning': 'Only completed pipe transitions change rooms. X wrapping is not a room transition. Restoring a checkpoint restores this route position.'}
