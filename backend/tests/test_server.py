"""Real HTTP/WebSocket API and a spawned worker using the SDK HTTP adapter."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
import yaml
from websockets.sync.client import connect


@pytest.mark.parametrize('launcher', ['uvicorn', 'stage_monitor'])
def test_real_http_websocket_server(tmp_path, launcher):
    class SensorEndpoint(BaseHTTPRequestHandler):
        output_enabled = False

        def log_message(self, *args):
            pass

        def respond(self, body):
            data=json.dumps(body).encode()
            self.send_response(200)
            self.send_header('Content-Length',str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self.respond({'temperature':22.4,'humidity':45,'output':self.output_enabled})

        def do_PUT(self):
            body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            SensorEndpoint.output_enabled=body['enabled']
            self.respond({'accepted':True})

    sensor=ThreadingHTTPServer(('127.0.0.1',0),SensorEndpoint)
    sensor_thread=threading.Thread(target=sensor.serve_forever,daemon=True)
    sensor_thread.start()
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        port=sock.getsockname()[1]
    root=Path(__file__).resolve().parents[2]
    config=tmp_path/'config.yaml'
    config.write_text(yaml.safe_dump({'mock_mode':False,
        'plugin_dirs':[str(root/'examples/plugins')]}),encoding='utf-8')
    env=dict(os.environ)
    env['STAGE_DATA_DIR']=str(tmp_path/'data')
    env['STAGE_ADMIN_PASSWORD']='integration-admin-password'
    env['STAGE_CONFIG']=str(config)
    log_path=tmp_path/'server.log'
    with log_path.open('w',encoding='utf-8') as output:
        command=[sys.executable,'-m',launcher]
        if launcher=='uvicorn':command+=['stage_monitor.main:app','--workers','1']
        command+=['--host','127.0.0.1','--port',str(port)]
        process=subprocess.Popen(command,env=env,stdout=output,stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        try:
            with httpx.Client(base_url=f'http://127.0.0.1:{port}',trust_env=False,timeout=5) as client:
                deadline=time.monotonic()+25
                while True:
                    try:
                        if client.get('/health/ready').status_code==200:break
                    except httpx.TransportError:pass
                    assert time.monotonic()<deadline,log_path.read_text(encoding='utf-8')
                    assert process.poll() is None,log_path.read_text(encoding='utf-8')
                    time.sleep(.1)
                login=client.post('/api/auth/login',json={'username':'admin','password':env['STAGE_ADMIN_PASSWORD']})
                assert login.status_code==200
                csrf=login.json()['csrf_token']
                assert client.get('/api/devices').json()==[]
                catalog=client.get('/api/plugins').json()
                assert all(not p['plugin_id'].startswith('mock.') for p in catalog)
                assert any(p['plugin_id']=='example.http_sensor' for p in catalog)
                response=client.post('/api/devices',headers={'X-CSRF-Token':csrf},json={
                    'name':'HTTP SDK integration','plugin_id':'example.http_sensor','category':'environment',
                    'endpoint':{'transport':'http','host':'127.0.0.1','port':sensor.server_port},
                    'custom_config':{'enable_control':True},'poll_interval':.2,'stale_after':5})
                assert response.status_code==201,response.text
                device_id=response.json()['id']
                while client.get(f'/api/devices/{device_id}/state').json()['status']!='online':
                    assert time.monotonic()<deadline,log_path.read_text(encoding='utf-8')
                    time.sleep(.1)
                with connect(f'ws://127.0.0.1:{port}/ws',origin='http://localhost:8080',
                    additional_headers={'Cookie':f"stage_session={client.cookies.get('stage_session')}"}) as websocket:
                    websocket.send(json.dumps({'csrf_token':csrf,'device_ids':[device_id]}))
                    assert json.loads(websocket.recv(timeout=5))['type']=='snapshot'
                    response=client.post(f'/api/devices/{device_id}/commands',headers={'X-CSRF-Token':csrf},
                        json={'command':'set_output','parameters':{'enabled':True}})
                    assert response.status_code==202,response.text
                    command_id=response.json()['command_id']
                    while True:
                        event=json.loads(websocket.recv(timeout=5))
                        if event['type']=='device.command.completed':
                            assert event['payload']['command_id']==command_id
                            assert event['payload']['confirmed']
                            break
                    assert SensorEndpoint.output_enabled
        finally:
            if process.poll() is None:
                if os.name=='nt':
                    subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],
                        capture_output=True,check=False,creationflags=subprocess.CREATE_NO_WINDOW)
                else:process.terminate()
                process.wait(timeout=10)
            sensor.shutdown()
            sensor.server_close()
            sensor_thread.join(timeout=2)
