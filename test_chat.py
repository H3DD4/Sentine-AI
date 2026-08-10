import urllib.request
import urllib.error
import json

base = 'http://localhost:8000'
auth = {'username': 'testuser99', 'password': 'password'}

# Register
req = urllib.request.Request(f'{base}/auth/register', data=json.dumps({'username': 'testuser99', 'email': 't99@test.com', 'password': 'password'}).encode('utf-8'), headers={'Content-Type':'application/json'})
try:
    urllib.request.urlopen(req)
except urllib.error.HTTPError as e:
    pass # maybe already exists

# Login
import urllib.parse
data = urllib.parse.urlencode({'username': 'testuser99', 'password': 'password'}).encode('utf-8')
req = urllib.request.Request(f'{base}/auth/login', data=data)
resp = urllib.request.urlopen(req)
token = json.loads(resp.read().decode('utf-8'))['access_token']

# Chat stream
req = urllib.request.Request(f'{base}/chat/stream', data=json.dumps({'messages': [{'role': 'user', 'content': 'Hello'}]}).encode('utf-8'), headers={'Content-Type':'application/json', 'Authorization': f'Bearer {token}'})
try:
    resp = urllib.request.urlopen(req)
    for line in resp:
        print(line.decode('utf-8').strip())
except urllib.error.HTTPError as e:
    print('HTTP ERROR', e.code)
    print(e.read().decode('utf-8'))
