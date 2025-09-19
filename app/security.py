"""
Security module for watibot3 webhook authentication and intrusion detection.
Implements passkey authentication and fail2ban integration for immediate IP blocking.
"""

import logging
import os
import subprocess
from typing import Optional, Dict, Any
from fastapi import Request, HTTPException
import secrets
import hashlib

logger = logging.getLogger(__name__)

# Load passkey from environment or generate a secure default
WATIBOT4_PASSKEY = os.getenv("WATIBOT4_PASSKEY", 'your_secure_passkey_here_change_me')

def generate_secure_passkey() -> str:
    """Generate a cryptographically secure passkey for webhook authentication."""
    return secrets.token_urlsafe(32)

def hash_passkey(passkey: str) -> str:
    """Create a SHA-256 hash of the passkey for logging (never log raw passkey)."""
    return hashlib.sha256(passkey.encode()).hexdigest()[:8]

def get_client_ip(request: Request) -> str:
    """Extract real client IP from request, handling proxies and load balancers."""
    # Check common proxy headers first
    forwarded_for = request.headers.get('X-Forwarded-For')
    if forwarded_for:
        # X-Forwarded-For can contain multiple IPs, use the first one
        return forwarded_for.split(',')[0].strip()
    
    real_ip = request.headers.get('X-Real-IP')
    if real_ip:
        return real_ip.strip()
    
    # Fallback to direct client IP
    return str(request.client.host) if request.client else "unknown"

def trigger_fail2ban_ban(ip_address: str, reason: str = "webhook_auth_failure") -> bool:
    """
    Immediately trigger fail2ban to permanently ban an IP address.
    
    Args:
        ip_address: The IP to ban
        reason: Reason for the ban (for logging)
    
    Returns:
        True if ban was successful, False otherwise
    """
    try:
        # Execute fail2ban command to immediately ban the IP
        result = subprocess.run([
            '/usr/bin/sudo', '/usr/bin/fail2ban-client', 'set', 'watibot-critical', 'banip', ip_address
        ], capture_output=True, text=True, timeout=10)
        
        if result.returncode == 0:
            logger.critical(f"[SECURITY] Successfully banned IP {ip_address} via fail2ban")
        else:
            logger.error(f"[SECURITY] Failed to ban IP {ip_address}: {result.stderr}")
    except Exception as e:
        logger.error(f"[SECURITY] Exception while banning IP {ip_address}: {e}")
        return False

def log_security_event(
    event_type: str, 
    ip_address: str, 
    details: Dict[str, Any], 
    severity: str = "WARNING"
) -> None:
    """
    Log security events with structured data for monitoring and analysis.
    
    Args:
        event_type: Type of security event (auth_failure, auth_success, etc.)
        ip_address: Source IP address
        details: Additional event details
        severity: Log severity level
    """
    log_entry = {
        "event": event_type,
        "ip": ip_address,
        "severity": severity,
        **details
    }
    
    if severity == "CRITICAL":
        logger.critical(f"[SECURITY] {log_entry}")
    elif severity == "ERROR":
        logger.error(f"[SECURITY] {log_entry}")
    else:
        logger.warning(f"[SECURITY] {log_entry}")

def validate_webhook_auth(request: Request, payload: Dict[str, Any]) -> bool:
    """
    Validate webhook authentication using passkey in request body.
    
    Args:
        request: FastAPI request object
        payload: Parsed JSON payload from request body
    
    Returns:
        True if authentication is valid, False otherwise
    
    Raises:
        HTTPException: If authentication fails (after logging and banning)
    """
    client_ip = get_client_ip(request)
    
    # Check if passkey exists in payload
    provided_passkey = payload.get('passkey')
    
    if not provided_passkey:
        log_security_event(
            "auth_failure_missing_passkey",
            client_ip,
            {
                "reason": "Missing passkey in request body",
                "payload_keys": list(payload.keys()),
                "user_agent": request.headers.get('User-Agent', 'unknown')
            },
            severity="CRITICAL"
        )
        
        # Trigger immediate permanent ban
        trigger_fail2ban_ban(client_ip, "missing_passkey")
        
        raise HTTPException(
            status_code=401,
            detail="Authentication required"
        )
    
    # Validate passkey
    if provided_passkey != WATIBOT4_PASSKEY:
        log_security_event(
            "auth_failure_invalid_passkey",
            client_ip,
            {
                "reason": "Invalid passkey provided",
                "passkey_hash": hash_passkey(provided_passkey),
                "expected_hash": hash_passkey(WATIBOT4_PASSKEY),
                "user_agent": request.headers.get('User-Agent', 'unknown')
            },
            severity="CRITICAL"
        )
        
        # Trigger immediate permanent ban
        trigger_fail2ban_ban(client_ip, "invalid_passkey")
        
        raise HTTPException(
            status_code=401,
            detail="Authentication failed"
        )
    
    # Authentication successful
    log_security_event(
        "auth_success",
        client_ip,
        {
            "reason": "Valid passkey provided",
            "user_agent": request.headers.get('User-Agent', 'unknown')
        },
        severity="INFO"
    )
    
    return True

def create_authenticated_payload_example() -> Dict[str, Any]:
    """
    Create an example payload showing how to include the passkey.
    Useful for documentation and testing.
    """
    return {
        "passkey": WEBHOOK_PASSKEY,
        "waId": "1234567890",
        "text": "Hello from authenticated client",
        "dataType": "text"
    }

def check_security_config() -> Dict[str, Any]:
    """
    Check current security configuration and return status.
    """
    expected_passkey = os.getenv("WATIBOT4_PASSKEY", 'your_secure_passkey_here_change_me')
    status = {
        "passkey_configured": expected_passkey != 'your_secure_passkey_here_change_me',
        "passkey_hash": hash_passkey(expected_passkey),
        "fail2ban_available": False
    }
    
    if not expected_passkey or expected_passkey == 'your_secure_passkey_here_change_me':
        logger.warning("[SECURITY] WATIBOT4_PASSKEY not set or using default - this is insecure!")
        expected_passkey = 'your_secure_passkey_here_change_me' 

    try:
        result = subprocess.run(['sudo', 'fail2ban-client', 'status'], 
                              capture_output=True, timeout=5)
        status["fail2ban_available"] = result.returncode == 0
    except:
        pass
    
    return status
