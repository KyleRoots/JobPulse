# Reference Number Preservation Process

## Guidelines for XML Rebuild Operations

### When to Preserve Reference Numbers (Ad-hoc Fixes)
Use `--preserve-references` flag when:
- Fixing data corruption or formatting issues
- Correcting job information errors  
- Resolving CDATA or HTML formatting problems
- Any fix that should maintain existing integrations

**Command:**
```bash
python rebuild_xml_standalone.py --preserve-references
```

### When to Generate New Reference Numbers (Scheduled Automation)
Use default behavior (no flag) when:
- Running comprehensive scheduled rebuilds
- Fixing major data inconsistencies that affect job mapping
- Resolving false monitoring alerts that indicate structural problems

**Command:**
```bash
python rebuild_xml_standalone.py
```

## Process Verification

After any rebuild operation:
1. Check job count matches expected tearsheet totals
2. Verify CDATA formatting is preserved
3. Confirm LinkedIn tags are maintained
4. Test live website functionality
5. Monitor for false positive alerts

## Emergency Rollback

If reference number changes cause issues:
1. Check xml_backups/ directory for automatic backups
2. Use XML safeguards rollback functionality if available
3. Contact system administrator for database restoration if needed

## Key Points

- **External integrations** may depend on stable reference numbers
- **Bookmarks and saved links** use reference numbers for job identification
- **Ad-hoc fixes should preserve** existing reference numbers unless specifically needed
- **Scheduled automation may generate new** reference numbers for data consistency