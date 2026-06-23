# Chocolatey Push Instructions for v1.1.3

## ⚠️ Requires Windows Machine

Chocolatey packages are best built and pushed from Windows due to NuGet tooling.

## Steps to Push from Windows:

### 1. Install Chocolatey
```powershell
Set-ExecutionPolicy Bypass -Scope Process -Force
[System.Net.ServicePointManager]::SecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol -bor 3072
iex ((New-Object System.Net.WebClient).DownloadString('https://community.chocolatey.org/install.ps1'))
```

### 2. Clone Repo and Navigate
```powershell
git clone https://github.com/EmpiricaAI/empirica.git
cd empirica/packaging/chocolatey
```

### 3. Build Package
```powershell
choco pack
```

This creates: `empirica.1.1.3.nupkg`

### 4. Test Locally (Optional)
```powershell
choco install empirica -s . -y
empirica --version
choco uninstall empirica -y
```

### 5. Set API Key
```powershell
$API_KEY = "a4c40d0c-b009-44e5-aa1e-409192a578e6"
choco apikey --key $API_KEY --source https://push.chocolatey.org/
```

### 6. Push to Chocolatey
```powershell
choco push empirica.1.1.3.nupkg --source https://push.chocolatey.org/
```

## What Happens Next:

1. **Moderator Review** (First Time):
   - Takes 24-72 hours
   - Check status: https://community.chocolatey.org/packages/empirica/1.1.3
   - You'll receive email notifications

2. **After Approval**:
   - Package goes live on Chocolatey Community
   - Future updates auto-approved (usually)
   - Users install: `choco install empirica`

## Alternative: Wait for User Demand

Since Windows isn't the primary platform for AI tools:
- Users can use: `pip install empirica` on Windows
- Submit to Chocolatey when Windows users request it

## Files Ready:
- ✅ empirica.nuspec (v1.1.3)
- ✅ tools/chocolateyinstall.ps1 (SHA256 updated)
- ✅ tools/chocolateyuninstall.ps1

