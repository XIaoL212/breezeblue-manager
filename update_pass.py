import sqlite3
import hashlib

db = sqlite3.connect('data/breezeblue.db')
cursor = db.cursor()

# Update admin password hash
new_hash = hashlib.sha256('55588800qq'.encode()).hexdigest()
cursor.execute('UPDATE admins SET password_hash=? WHERE username=?', (new_hash, 'XIaoL'))
db.commit()

# Verify
cursor.execute('SELECT username, password_hash FROM admins')
for row in cursor.fetchall():
    print(f'User: {row[0]}, Hash: {row[1][:16]}...')

db.close()
print('Password updated successfully')
