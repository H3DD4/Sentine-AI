import urllib.request
import urllib.parse
import json
import time

# Login
form = urllib.parse.urlencode({'username': 'testuser2@test.com', 'password': 'testpass123'}).encode()
req = urllib.request.Request('http://localhost:8000/auth/login', data=form, headers={'Content-Type': 'application/x-www-form-urlencoded'})
resp = urllib.request.urlopen(req)
token_data = json.loads(resp.read())
token = token_data.get('access_token', '')
print('Login ok, token length:', len(token))

# Test with a real finding message
chat_data = json.dumps({'messages': [{'role': 'user', 'content': 'What is CVE-2023-50164?'}]}).encode()
req2 = urllib.request.Request(
    'http://localhost:8000/chat/stream',
    data=chat_data,
    headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token}
)
print('Sending request at:', time.strftime('%H:%M:%S'))
try:
    resp2 = urllib.request.urlopen(req2, timeout=120)
    print('Status:', resp2.status, 'at:', time.strftime('%H:%M:%S'))
    count = 0
    for line in resp2:
        decoded = line.decode('utf-8').strip()
        if decoded:
            ts = time.strftime('%H:%M:%S')
            print('[' + ts + '] FRAME:', decoded[:400])
        count += 1
        if count > 20:
            break
    print('=== DONE ===')
except Exception as e:
    print('ERROR:', type(e).__name__, str(e)[:300])
    if hasattr(e, 'read'):
        print(e.read().decode('utf-8')[:500])
