# Sims Together Stage 2: safe manual game-to-launcher handshake.
# Built for The Sims 4 Python 3.7. No save, lot, or simulation changes.
import json
import urllib.request
import sims4.commands

BRIDGE = 'http://127.0.0.1:51246'


def _request(method, endpoint, body=None):
    data = None if body is None else json.dumps(body).encode('utf-8')
    request = urllib.request.Request(BRIDGE + endpoint, data=data, method=method)
    if data is not None:
        request.add_header('Content-Type', 'application/json')
    with urllib.request.urlopen(request, timeout=1.5) as response:
        return json.loads(response.read(2048).decode('utf-8'))


@sims4.commands.Command('st.ping', command_type=sims4.commands.CommandType.Live)
def st_ping(_connection=None):
    output = sims4.commands.CheatOutput(_connection)
    try:
        reply = _request('POST', '/game-event', {'event': 'ping'})
        if reply.get('ok'):
            output('Sims Together: game connected to local launcher!')
            if reply.get('lobby_connected'):
                output('Sims Together: LAN lobby is connected too.')
            else:
                output('Sims Together: launcher ready, but not in LAN lobby.')
        else:
            output('Sims Together: bridge gave an unexpected response.')
    except Exception as error:
        output('Sims Together: cannot reach launcher. Open v0.2 first.')
        output(str(error)[:180])


@sims4.commands.Command('st.status', command_type=sims4.commands.CommandType.Live)
def st_status(_connection=None):
    output = sims4.commands.CheatOutput(_connection)
    try:
        reply = _request('GET', '/status')
        output('Sims Together: launcher v' + str(reply.get('version', '?')) + ' found.')
        output('Sims Together: lobby connected = ' + str(bool(reply.get('lobby_connected'))))
    except Exception as error:
        output('Sims Together: launcher not reachable: ' + str(error)[:160])
