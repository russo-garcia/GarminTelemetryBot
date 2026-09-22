from google_auth_oauthlib.flow import InstalledAppFlow

# Initialize the OAuth flow
flow = InstalledAppFlow.from_client_secrets_file('client_secrets.json', ['https://www.googleapis.com/auth/drive.file'])

# Open the browser to authenticate
creds = flow.run_local_server(port=0)

# Save the resulting credentials
with open('token.json', 'w') as f:
    f.write(creds.to_json())
print("Success! token.json has been created.")

