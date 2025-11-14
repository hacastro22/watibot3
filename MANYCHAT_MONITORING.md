# ManyChat Monitoring & Testing Quick Reference

## Service Management

### Restart Service (Apply Fixes)
```bash
sudo systemctl restart watibot4
```

### Check Service Status
```bash
sudo systemctl status watibot4
```

### View Recent Logs
```bash
sudo journalctl -u watibot4 -f
```

---

## Testing Commands

### Test Conversation Logging
```bash
cd /home/robin/watibot4
source venv/bin/activate
python test_conversation_log.py
```

### Test Message Splitting
```bash
cd /home/robin/watibot4
source venv/bin/activate
python test_message_splitter.py
```

### Test Instagram Menu (Manual)
```bash
curl -X POST http://127.0.0.1:8006/manychat/webhook \
  -H 'Content-Type: application/json' \
  -d '{
    "platform": "manychat",
    "subscriber": {
      "id": "1463178150",
      "ig_id": "1463178150",
      "ig_username": "test_user"
    },
    "message": {"text": "Me podría mostrar la carta de comida"},
    "passkey": "FuK@tTcKerZ-2o25"
  }'
```

---

## Log Monitoring

### Check for Message Splitting
```bash
sudo journalctl -u watibot4 --since "10 minutes ago" | grep "Message exceeds 2000 chars"
```

### Check for Context Injection
```bash
sudo journalctl -u watibot4 --since "10 minutes ago" | grep "Retrieved.*messages for context injection"
```

### Check for Thread Rotation
```bash
sudo journalctl -u watibot4 --since "10 minutes ago" | grep "THREAD_ROTATION"
```

### Check for Failed Messages
```bash
sudo journalctl -u watibot4 --since "1 hour ago" | grep -E "(Failed to send|Error sending)"
```

---

## Database Management

### Check Conversation Log Size
```bash
ls -lh /home/robin/watibot4/app/conversation_log.db
```

### Count Messages in Log
```bash
sqlite3 /home/robin/watibot4/app/conversation_log.db \
  "SELECT COUNT(*) as total_messages FROM conversation_log;"
```

### View Recent Messages
```bash
sqlite3 /home/robin/watibot4/app/conversation_log.db \
  "SELECT timestamp, channel, role, substr(content, 1, 50) as preview 
   FROM conversation_log 
   ORDER BY timestamp DESC 
   LIMIT 10;"
```

### Cleanup Old Messages (30+ days)
```bash
cd /home/robin/watibot4
source venv/bin/activate
python -c "from app.conversation_log import cleanup_old_messages; cleanup_old_messages(30)"
```

---

## Issue Investigation

### Investigate Specific User
```bash
# Replace USER_ID with actual subscriber_id
USER_ID="24958501110451908"

# View conversation history
sqlite3 /home/robin/watibot4/app/conversation_log.db \
  "SELECT timestamp, role, content 
   FROM conversation_log 
   WHERE user_identifier='$USER_ID' 
   ORDER BY timestamp DESC 
   LIMIT 20;"

# Check logs for this user
sudo journalctl -u watibot4 --since "1 hour ago" | grep "$USER_ID"
```

### Check for Context Window Issues
```bash
sudo journalctl -u watibot4 --since "1 hour ago" | grep "context_length_exceeded"
```

### Check ManyChat API Errors
```bash
sudo journalctl -u watibot4 --since "1 hour ago" | grep -E "\[FB\].*Error|\[IG\].*Error"
```

---

## Performance Monitoring

### Database Growth Rate
```bash
# Check size daily
watch -n 3600 'ls -lh /home/robin/watibot4/app/conversation_log.db'
```

### Message Split Statistics
```bash
# Count how many messages were split in last hour
sudo journalctl -u watibot4 --since "1 hour ago" | grep "splitting into" | wc -l
```

### Context Injection Statistics
```bash
# See how many context injections happened
sudo journalctl -u watibot4 --since "1 hour ago" | grep "Generated context injection" | wc -l
```

---

## Health Checks

### Full System Check
```bash
echo "=== Service Status ==="
systemctl is-active watibot4

echo -e "\n=== Database Status ==="
ls -lh /home/robin/watibot4/app/conversation_log.db

echo -e "\n=== Recent Errors ==="
sudo journalctl -u watibot4 --since "10 minutes ago" | grep -i error | tail -5

echo -e "\n=== Message Stats ==="
sqlite3 /home/robin/watibot4/app/conversation_log.db \
  "SELECT channel, COUNT(*) as count 
   FROM conversation_log 
   GROUP BY channel;"
```

---

## Emergency Procedures

### If Service Won't Start
```bash
# Check for Python errors
sudo journalctl -u watibot4 -n 50 --no-pager

# Check for port conflicts
sudo netstat -tulpn | grep 8006

# Restart with verbose logging
sudo systemctl restart watibot4
sudo journalctl -u watibot4 -f
```

### If Messages Not Being Logged
```bash
# Check database permissions
ls -la /home/robin/watibot4/app/conversation_log.db

# Verify module can be imported
cd /home/robin/watibot4
source venv/bin/activate
python -c "from app import conversation_log; print('OK')"
```

### If Context Not Being Injected
```bash
# Check for context retrieval attempts
sudo journalctl -u watibot4 --since "10 minutes ago" | grep "MANYCHAT.*context"

# Verify database has messages
sqlite3 /home/robin/watibot4/app/conversation_log.db \
  "SELECT COUNT(*) FROM conversation_log WHERE channel IN ('facebook', 'instagram');"
```

---

## Useful Queries

### Top Active Users (Last 24h)
```bash
sqlite3 /home/robin/watibot4/app/conversation_log.db \
  "SELECT user_identifier, channel, COUNT(*) as message_count
   FROM conversation_log
   WHERE timestamp > datetime('now', '-24 hours')
   GROUP BY user_identifier, channel
   ORDER BY message_count DESC
   LIMIT 10;"
```

### Average Message Length by Channel
```bash
sqlite3 /home/robin/watibot4/app/conversation_log.db \
  "SELECT channel, 
          AVG(LENGTH(content)) as avg_length,
          MAX(LENGTH(content)) as max_length
   FROM conversation_log
   WHERE role='assistant'
   GROUP BY channel;"
```

### Messages That Would Need Splitting
```bash
sqlite3 /home/robin/watibot4/app/conversation_log.db \
  "SELECT timestamp, user_identifier, LENGTH(content) as length
   FROM conversation_log
   WHERE role='assistant' 
   AND LENGTH(content) > 2000
   ORDER BY timestamp DESC
   LIMIT 10;"
```

---

## Backup & Restore

### Backup Conversation Log
```bash
cp /home/robin/watibot4/app/conversation_log.db \
   /home/robin/watibot4/backups/conversation_log_$(date +%Y%m%d).db
```

### Restore from Backup
```bash
# Stop service first
sudo systemctl stop watibot4

# Restore database
cp /home/robin/watibot4/backups/conversation_log_20251009.db \
   /home/robin/watibot4/app/conversation_log.db

# Restart service
sudo systemctl start watibot4
```

---

## Alert Thresholds

Monitor these metrics:
- ✅ **Database size**: Alert if > 100MB
- ✅ **Split rate**: Alert if > 10% of messages need splitting
- ✅ **Failed sends**: Alert if > 5 failures per hour
- ✅ **Context retrieval**: Alert if retrieval time > 100ms
- ✅ **Thread rotations**: Alert if > 5 per hour per user

---

**Last Updated**: October 9, 2025
