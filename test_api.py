import requests

r = requests.post('http://127.0.0.1:8001/api/login', json={'username': 'XIaoL', 'password': 'breezeblue-admin'})
token = r.json()['token']
headers = {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}

# Add account
r = requests.post('http://127.0.0.1:8001/api/accounts', headers=headers, json={'email': 'user@example.com', 'password': 'pass123', 'note': 'Test user'})
print('Add account:', r.status_code)

# Get accounts
r = requests.get('http://127.0.0.1:8001/api/accounts', headers=headers)
accounts = r.json()
print('Accounts:', len(accounts))
for a in accounts:
    print('  ' + a['email'] + ' - credits: ' + str(a['credits']) + ', streak: ' + str(a['checkin_streak']))

# Checkin first account
if accounts:
    url = 'http://127.0.0.1:8001/api/accounts/' + str(accounts[0]['id']) + '/checkin'
    r = requests.post(url, headers=headers)
    print('Checkin:', r.status_code, r.json())
