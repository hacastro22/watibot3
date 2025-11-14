# Playwright Auto-Setup Documentation

## 🎯 Problem Solved
watibot4 was experiencing daily Playwright browser failures due to service restarts that lost browser cache. This solution ensures browsers are automatically available after every restart.

## 🛠️ Components Installed

### 1. **setup_playwright.sh**
- **Purpose**: Ensures Playwright browsers are installed and working
- **Runs**: Every service startup (automatically)
- **Features**: 
  - Version-aware browser installation
  - Compatibility with multiple Playwright versions (1.44.0 and 1.48.0)
  - Manual fallback for specific browser versions
  - Functionality testing

### 2. **start_watibot4.sh**
- **Purpose**: Main startup script that runs Playwright setup before starting the app
- **Features**:
  - Pre-flight dependency checks
  - Automatic Playwright setup
  - Graceful error handling
  - Comprehensive logging

### 3. **fix_playwright.sh**
- **Purpose**: Emergency recovery script for manual intervention
- **Usage**: `./fix_playwright.sh`
- **Features**:
  - Force reinstall Playwright
  - Download compatible browser versions
  - Full system recovery

### 4. **check_health.sh**
- **Purpose**: Health monitoring and diagnostics
- **Usage**: `./check_health.sh`
- **Features**:
  - Service status check
  - Playwright installation verification
  - Browser availability check
  - Live browser launch test
  - Disk usage monitoring

### 5. **Updated systemd service**
- **Changes**:
  - Uses `start_watibot4.sh` as entry point
  - Extended startup timeout (300s)
  - Increased restart delay (10s)
  - Playwright environment variables

## 🚀 How It Works

1. **Service starts** → systemd calls `start_watibot4.sh`
2. **Setup runs** → `setup_playwright.sh` ensures browsers are ready
3. **Verification** → All dependencies checked
4. **App starts** → Main uvicorn application launches
5. **If restart** → Process repeats automatically

## 🔧 Manual Operations

### Check System Health
```bash
./check_health.sh
```

### Force Playwright Recovery
```bash
./fix_playwright.sh
```

### Manual Setup Test
```bash
./setup_playwright.sh
```

### Restart Service
```bash
sudo systemctl restart watibot4.service
```

### View Logs
```bash
sudo journalctl -u watibot4.service -f
```

## 📊 Monitoring

The system now automatically handles:
- ✅ Daily service restarts
- ✅ Playwright browser cache losses  
- ✅ Version compatibility issues
- ✅ Browser download failures
- ✅ Dependency verification

## 🎯 Result
**No more manual Playwright interventions needed!** The service is now self-healing and restart-proof.
