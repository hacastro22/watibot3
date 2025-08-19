# WatiBot3 Vertex AI - Production Rollback Plan

**Document Version**: 1.0  
**Date**: August 19, 2025  
**Migration Status**: Production Ready

## Emergency Rollback Procedure

### Instant Rollback (< 1 minute)

**When to Use**: Critical production issues, API failures, or unexpected behavior

#### Step 1: Disable Vertex AI Flag
```bash
# Edit production .env file
nano /home/robin/watibot3/.env

# Change this line:
USE_VERTEX_AI=false
```

#### Step 2: Restart Service
```bash
cd /home/robin/watibot3
sudo systemctl restart watibot3.service
```

#### Step 3: Verify Rollback
```bash
# Check service status
sudo systemctl status watibot3.service

# Monitor logs for OpenAI routing
tail -f /var/log/watibot3/application.log | grep -E "(OpenAI|VERTEX)"

# Look for: [OPENAI] instead of [VERTEX] in logs
```

### Rollback Validation Checklist

- [ ] **Service Status**: `systemctl status watibot3.service` shows active (running)
- [ ] **Routing Confirmed**: Logs show OpenAI API calls, not Vertex AI
- [ ] **Conversations Working**: Test conversation receives proper response
- [ ] **All Features Active**: Vision, audio, and text processing functional
- [ ] **Response Time**: < 5 seconds for standard conversations
- [ ] **No Data Loss**: All conversation history preserved

## Rollback Scenarios & Solutions

### Scenario 1: Vertex AI API Quota Exceeded

**Symptoms**:
- Error: "Quota exceeded for aiplatform.googleapis.com"
- High response latency
- Failed conversation processing

**Immediate Action**:
```bash
# Emergency rollback to OpenAI
USE_VERTEX_AI=false
sudo systemctl restart watibot3.service
```

**Resolution**: Contact Google Cloud Support to increase quota limits

### Scenario 2: Authentication/Permissions Issues

**Symptoms**:
- Error: "Permission denied for Vertex AI"
- 403 Forbidden responses
- Service account authentication failures

**Immediate Action**:
```bash
# Rollback to OpenAI while fixing credentials
USE_VERTEX_AI=false
sudo systemctl restart watibot3.service
```

**Resolution**: Verify service account permissions and credential file access

### Scenario 3: High Error Rate (>5%)

**Symptoms**:
- Increased conversation failures
- Timeout errors
- Poor response quality

**Immediate Action**:
```bash
# Rollback to stable OpenAI system
USE_VERTEX_AI=false
sudo systemctl restart watibot3.service
```

**Resolution**: Investigate error patterns and optimize Vertex AI configuration

### Scenario 4: Performance Degradation

**Symptoms**:
- Response times >10 seconds
- Customer complaints about slow responses
- Timeout errors

**Immediate Action**:
```bash
# Return to known-fast OpenAI system
USE_VERTEX_AI=false
sudo systemctl restart watibot3.service
```

**Resolution**: Analyze performance bottlenecks and optimize implementation

## Post-Rollback Verification

### Immediate Checks (First 5 minutes)
```bash
# 1. Verify service health
curl -X POST http://localhost:8003/webhook \
  -H "Content-Type: application/json" \
  -d '{"test": "rollback_verification"}'

# 2. Check conversation processing
tail -f /var/log/watibot3/application.log

# 3. Monitor error rates
grep -c "ERROR" /var/log/watibot3/application.log
```

### Extended Validation (First 30 minutes)
- [ ] **Text Conversations**: Test standard Q&A flow
- [ ] **Image Processing**: Test image classification
- [ ] **Audio Processing**: Test voice message transcription
- [ ] **Payment Processing**: Test payment proof analysis
- [ ] **Database Integrity**: Verify all conversation data intact
- [ ] **Performance Metrics**: Confirm response times normalized

## Data Safety Guarantees

### Zero Data Loss Architecture
- **Conversation History**: All conversations stored with original `thread_id` preserved
- **Session Mapping**: Vertex AI `session_id` columns independent of core data
- **Rollback Safety**: OpenAI integration uses original database schema
- **Context Preservation**: Full conversation context available post-rollback

### Database State During Rollback
```sql
-- Conversations remain accessible via original thread_id
SELECT thread_id, phone_number, created_at 
FROM conversations 
WHERE thread_id IS NOT NULL;

-- Vertex session data preserved for future re-migration
SELECT session_id, vertex_migrated, migration_date 
FROM conversations 
WHERE vertex_migrated = 1;
```

## Re-Migration Strategy

### When to Re-Attempt Vertex AI Migration

1. **Issue Resolution Confirmed**: Root cause identified and fixed
2. **Testing Completed**: Staging environment validates solution
3. **Low-Risk Window**: Deploy during low-traffic period
4. **Monitoring Ready**: Enhanced monitoring in place

### Re-Migration Process
```bash
# 1. Verify fix in staging environment
python3 test_vertex_agent_validation.py

# 2. Enable Vertex AI flag
USE_VERTEX_AI=true

# 3. Gradual rollout
sudo systemctl restart watibot3.service

# 4. Enhanced monitoring for first 2 hours
tail -f /var/log/watibot3/application.log | grep -E "(ERROR|VERTEX|timeout)"
```

## Emergency Contacts & Escalation

### Level 1: Immediate Response
- **Technical Lead**: Primary rollback decision maker
- **Operations Team**: Service restart and monitoring
- **On-Call Engineer**: 24/7 availability for critical issues

### Level 2: Extended Support
- **Google Cloud Support**: API and quota issues
- **Database Administrator**: Data integrity concerns
- **Customer Success**: Customer impact assessment

### Level 3: Executive Escalation
- **CTO/Technical Director**: Business continuity decisions
- **Customer Relations**: Major customer impact communication

## Rollback Communication Plan

### Internal Communication
```
ALERT: WatiBot3 Vertex AI Rollback Initiated
Time: [TIMESTAMP]
Reason: [ISSUE_DESCRIPTION]
Impact: [CUSTOMER_IMPACT_ASSESSMENT]
ETA to Resolution: [TIME_ESTIMATE]
```

### Customer Communication (if needed)
```
We experienced a brief technical issue that has been resolved. 
Service is now fully operational. We apologize for any inconvenience.
```

## Monitoring & Alerts

### Critical Metrics Post-Rollback
- **Response Time**: < 5 seconds (target)
- **Error Rate**: < 1% (acceptable)
- **Availability**: > 99.5% (SLA requirement)
- **Customer Satisfaction**: Maintain current levels

### Automated Alerts
```bash
# Setup monitoring for rollback success
#!/bin/bash
# Check if OpenAI routing is active after rollback
if grep -q "OpenAI" /var/log/watibot3/application.log; then
    echo "Rollback successful: OpenAI routing confirmed"
else
    echo "ALERT: Rollback may have failed - investigate immediately"
fi
```

---

## Rollback Decision Matrix

| Issue Severity | Response Time | Action |
|---|---|---|
| **Critical** (Service Down) | Immediate | Execute rollback, investigate later |
| **High** (>10% Error Rate) | < 5 minutes | Execute rollback, fix in staging |
| **Medium** (Performance Issues) | < 15 minutes | Evaluate impact, consider rollback |
| **Low** (Minor Issues) | Monitor | Fix in place, rollback if worsens |

---

**Remember**: The rollback is designed to be **instantaneous** and **zero-risk**. When in doubt, rollback first and investigate second. Customer experience is the top priority.

**Document Owner**: Technical Lead  
**Review Schedule**: After each rollback incident  
**Next Update**: Post-production deployment
