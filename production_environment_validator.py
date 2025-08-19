#!/usr/bin/env python3
"""
Production Environment Validator
Validates all prerequisites for Vertex AI production deployment
"""

import os
import json
import logging
import sys
import subprocess
from pathlib import Path

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def check_environment_variables():
    """Validate required environment variables"""
    logger.info("🔍 Checking Environment Variables...")
    
    required_vertex_vars = {
        'USE_VERTEX_AI': 'Feature flag for Vertex AI routing',
        'GOOGLE_CLOUD_PROJECT_ID': 'Google Cloud Project ID', 
        'VERTEX_AI_LOCATION': 'Vertex AI region location',
        'GOOGLE_APPLICATION_CREDENTIALS': 'Path to service account JSON'
    }
    
    required_openai_vars = {
        'OPENAI_API_KEY': 'OpenAI API key for fallback',
        'OPENAI_AGENT_ID': 'OpenAI Assistant ID for fallback'
    }
    
    issues = []
    
    # Check Vertex AI variables
    for var, description in required_vertex_vars.items():
        value = os.getenv(var)
        if not value:
            issues.append(f"❌ {var}: Not set ({description})")
        else:
            logger.info(f"✓ {var}: {'*' * min(len(value), 10)} ({description})")
    
    # Check OpenAI variables (for fallback)
    for var, description in required_openai_vars.items():
        value = os.getenv(var)
        if not value:
            issues.append(f"⚠️  {var}: Not set - fallback may not work ({description})")
        else:
            logger.info(f"✓ {var}: {'*' * min(len(value), 10)} ({description})")
    
    return len(issues) == 0, issues

def check_google_cloud_credentials():
    """Validate Google Cloud service account credentials"""
    logger.info("🔍 Checking Google Cloud Credentials...")
    
    creds_path = os.getenv('GOOGLE_APPLICATION_CREDENTIALS')
    if not creds_path:
        return False, ["❌ GOOGLE_APPLICATION_CREDENTIALS not set"]
    
    issues = []
    
    # Check file exists
    if not os.path.exists(creds_path):
        issues.append(f"❌ Credentials file not found: {creds_path}")
        return False, issues
    
    # Check file permissions
    file_stat = os.stat(creds_path)
    file_mode = oct(file_stat.st_mode)[-3:]
    if file_mode != '600':
        issues.append(f"⚠️  Credentials file permissions: {file_mode} (should be 600)")
    
    # Check file format
    try:
        with open(creds_path, 'r') as f:
            creds_data = json.load(f)
        
        required_keys = ['type', 'project_id', 'private_key', 'client_email']
        missing_keys = [key for key in required_keys if key not in creds_data]
        
        if missing_keys:
            issues.append(f"❌ Missing credential keys: {missing_keys}")
        else:
            logger.info(f"✓ Valid service account: {creds_data.get('client_email')}")
            logger.info(f"✓ Project ID: {creds_data.get('project_id')}")
    
    except json.JSONDecodeError:
        issues.append("❌ Invalid JSON in credentials file")
    except Exception as e:
        issues.append(f"❌ Error reading credentials: {e}")
    
    return len(issues) == 0, issues

def check_python_dependencies():
    """Validate required Python packages"""
    logger.info("🔍 Checking Python Dependencies...")
    
    required_packages = {
        'vertexai': '1.0.0',
        'google-cloud-aiplatform': '1.0.0', 
        'google-cloud-speech': '2.0.0',
        'openai': '1.0.0',
        'httpx': '0.20.0',
        'aiohttp': '3.8.0',
        'pillow': '8.0.0'
    }
    
    issues = []
    
    try:
        import pkg_resources
        installed_packages = {pkg.project_name.lower(): pkg.version 
                            for pkg in pkg_resources.working_set}
        
        for package, min_version in required_packages.items():
            package_lower = package.lower().replace('-', '_')
            alt_package = package.lower().replace('_', '-')
            
            if package_lower in installed_packages:
                version = installed_packages[package_lower]
                logger.info(f"✓ {package}: {version}")
            elif alt_package in installed_packages:
                version = installed_packages[alt_package]
                logger.info(f"✓ {package}: {version}")
            else:
                issues.append(f"❌ Missing package: {package}")
    
    except Exception as e:
        issues.append(f"❌ Error checking packages: {e}")
    
    return len(issues) == 0, issues

def check_database_schema():
    """Validate database schema supports Vertex AI"""
    logger.info("🔍 Checking Database Schema...")
    
    try:
        # Add app to path
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'app'))
        
        # Import with mock to avoid database connection
        from unittest.mock import patch, MagicMock
        
        with patch.dict('sys.modules', {
            'sqlite3': MagicMock(),
            'mysql.connector': MagicMock()
        }):
            import app.thread_store as thread_store
            
            required_functions = [
                'get_or_create_thread',
                'get_vertex_session_id',
                'set_vertex_session_id', 
                'migrate_thread_to_vertex_session'
            ]
            
            issues = []
            for func_name in required_functions:
                if hasattr(thread_store, func_name):
                    logger.info(f"✓ Database function: {func_name}")
                else:
                    issues.append(f"❌ Missing database function: {func_name}")
            
            return len(issues) == 0, issues
    
    except Exception as e:
        return False, [f"❌ Database schema check failed: {e}"]

def check_file_permissions():
    """Check critical file and directory permissions"""
    logger.info("🔍 Checking File Permissions...")
    
    critical_paths = [
        ('/home/robin/watibot3', 'Project directory'),
        ('/home/robin/watibot3/app', 'Application code'),
        ('/home/robin/watibot3/credentials', 'Credentials directory'),
        ('/var/log/watibot3', 'Log directory')
    ]
    
    issues = []
    
    for path, description in critical_paths:
        if os.path.exists(path):
            if os.access(path, os.R_OK):
                logger.info(f"✓ Read access: {path} ({description})")
            else:
                issues.append(f"❌ No read access: {path} ({description})")
                
            if os.path.isdir(path) and os.access(path, os.W_OK):
                logger.info(f"✓ Write access: {path} ({description})")
            elif os.path.isdir(path):
                issues.append(f"❌ No write access: {path} ({description})")
        else:
            issues.append(f"⚠️  Path not found: {path} ({description})")
    
    return len(issues) == 0, issues

def check_network_connectivity():
    """Test connectivity to required APIs"""
    logger.info("🔍 Checking Network Connectivity...")
    
    endpoints = [
        ('https://aiplatform.googleapis.com', 'Vertex AI API'),
        ('https://speech.googleapis.com', 'Google Cloud Speech API'),
        ('https://api.openai.com', 'OpenAI API (fallback)')
    ]
    
    issues = []
    
    for endpoint, description in endpoints:
        try:
            import urllib.request
            urllib.request.urlopen(endpoint, timeout=5)
            logger.info(f"✓ Network access: {description}")
        except Exception as e:
            issues.append(f"❌ Network issue: {description} - {str(e)[:50]}")
    
    return len(issues) == 0, issues

def check_system_resources():
    """Check system resources and capacity"""
    logger.info("🔍 Checking System Resources...")
    
    issues = []
    
    try:
        import psutil
        
        # Check memory
        memory = psutil.virtual_memory()
        memory_gb = memory.total / (1024**3)
        if memory_gb < 2:
            issues.append(f"⚠️  Low memory: {memory_gb:.1f}GB (recommend 4GB+)")
        else:
            logger.info(f"✓ Memory available: {memory_gb:.1f}GB")
        
        # Check disk space
        disk = psutil.disk_usage('/')
        disk_gb = disk.free / (1024**3)
        if disk_gb < 1:
            issues.append(f"❌ Low disk space: {disk_gb:.1f}GB free")
        else:
            logger.info(f"✓ Disk space: {disk_gb:.1f}GB free")
        
        # Check CPU
        cpu_count = psutil.cpu_count()
        logger.info(f"✓ CPU cores: {cpu_count}")
        
    except ImportError:
        logger.warning("psutil not available - skipping system resource check")
    except Exception as e:
        issues.append(f"⚠️  System resource check failed: {e}")
    
    return len(issues) == 0, issues

def run_production_validation():
    """Run all production environment validation checks"""
    logger.info("🚀 Starting Production Environment Validation")
    logger.info("=" * 70)
    
    checks = [
        ("Environment Variables", check_environment_variables),
        ("Google Cloud Credentials", check_google_cloud_credentials), 
        ("Python Dependencies", check_python_dependencies),
        ("Database Schema", check_database_schema),
        ("File Permissions", check_file_permissions),
        ("Network Connectivity", check_network_connectivity),
        ("System Resources", check_system_resources)
    ]
    
    all_issues = []
    passed_checks = 0
    
    for check_name, check_func in checks:
        logger.info(f"\n🧪 Running {check_name} check...")
        try:
            success, issues = check_func()
            if success:
                passed_checks += 1
                logger.info(f"✅ {check_name}: PASSED")
            else:
                logger.warning(f"⚠️  {check_name}: ISSUES FOUND")
                all_issues.extend(issues)
        except Exception as e:
            logger.error(f"💥 {check_name}: CHECK FAILED - {e}")
            all_issues.append(f"❌ {check_name} validation failed: {e}")
    
    # Summary
    logger.info("\n" + "=" * 70)
    logger.info(f"🎯 VALIDATION RESULTS: {passed_checks}/{len(checks)} checks passed")
    
    if all_issues:
        logger.warning("\n📋 ISSUES TO RESOLVE:")
        for issue in all_issues:
            logger.warning(f"  {issue}")
    
    if passed_checks == len(checks) and not all_issues:
        logger.info("🎉 PRODUCTION ENVIRONMENT VALIDATION PASSED!")
        logger.info("✅ System is ready for Vertex AI production deployment")
        return True
    else:
        logger.warning("⚠️  Production validation incomplete - resolve issues before deployment")
        return False

if __name__ == "__main__":
    try:
        success = run_production_validation()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        logger.info("\n🛑 Validation interrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"💥 Validation failed: {e}")
        sys.exit(1)
