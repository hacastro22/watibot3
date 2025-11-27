"""
Protocol Reference Validator and Fixer for system_instructions_new.txt

This tool validates that all protocol references in DECISION_TREE and MODULE_DEPENDENCIES
point to actually existing protocols, and can fix inconsistencies.

Run: python -m app.protocol_reference_validator
"""

import json
import re
import logging
from pathlib import Path
from typing import Dict, List, Tuple, Set, Optional

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)


# Known reference corrections (shorthand -> full path)
REFERENCE_CORRECTIONS = {
    # DEPENDENCY_CHAINS shorthand references
    "accommodations.bungalow_occupancy": "MODULE_4_INFORMATION.accommodations.occupancy_rules",
    "accommodations.room_details": "MODULE_4_INFORMATION.accommodations.bungalow_types",
    "accommodation_suggestion_logic": "MODULE_4_INFORMATION.accommodation_suggestion_logic",
    "occupancy_enforcement_protocol": "MODULE_2C_AVAILABILITY.occupancy_enforcement_protocol",
    "payment_methods": "MODULE_2B_PRICE_INQUIRY.payment_methods",
    "payment_objection_handling_protocol": "MODULE_2B_PRICE_INQUIRY.payment_objection_handling_protocol",
}


def load_instructions(file_path: str) -> dict:
    """Load and parse the system instructions JSON file."""
    with open(file_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def extract_module_references(obj: dict, current_path: str = "") -> Set[str]:
    """
    Recursively extract all module/protocol references from the instructions.
    
    Looks for keys like 'module', 'load', 'action' that contain module paths.
    """
    references = set()
    
    if isinstance(obj, dict):
        for key, value in obj.items():
            new_path = f"{current_path}.{key}" if current_path else key
            
            # Check for module reference keys
            if key in ('module', 'load', 'action', 'output', 'auto_load'):
                if isinstance(value, str) and 'MODULE_' in value:
                    # Extract module paths from the value
                    paths = re.findall(r'MODULE_[A-Z0-9_]+(?:\.[a-z_]+)+', value)
                    references.update(paths)
                elif isinstance(value, list):
                    for item in value:
                        if isinstance(item, str):
                            if 'MODULE_' in item:
                                references.add(item)
                            elif '.' in item or item.endswith('_protocol'):
                                references.add(item)  # Shorthand reference
            
            # Recurse into nested structures
            references.update(extract_module_references(value, new_path))
    
    elif isinstance(obj, list):
        for item in obj:
            references.update(extract_module_references(item, current_path))
    
    return references


def get_all_protocol_paths(obj: dict, current_path: str = "") -> Set[str]:
    """
    Recursively build a set of all actual paths in the instructions.
    """
    paths = set()
    
    if isinstance(obj, dict):
        for key, value in obj.items():
            new_path = f"{current_path}.{key}" if current_path else key
            paths.add(new_path)
            paths.update(get_all_protocol_paths(value, new_path))
    
    return paths


def validate_references(instructions: dict) -> Tuple[List[str], List[str], List[Tuple[str, str]]]:
    """
    Validate all module references exist.
    
    Returns:
        - valid_refs: List of valid references
        - invalid_refs: List of references that don't exist
        - fixable_refs: List of (old_ref, new_ref) that can be auto-fixed
    """
    # Get all references from DECISION_TREE and MODULE_DEPENDENCIES
    decision_tree_refs = extract_module_references(
        instructions.get('DECISION_TREE', {}), 
        'DECISION_TREE'
    )
    module_dep_refs = extract_module_references(
        instructions.get('MODULE_DEPENDENCIES', {}),
        'MODULE_DEPENDENCIES'
    )
    
    all_refs = decision_tree_refs | module_dep_refs
    
    # Get all actual paths in the document
    actual_paths = get_all_protocol_paths(instructions)
    
    valid_refs = []
    invalid_refs = []
    fixable_refs = []
    
    for ref in sorted(all_refs):
        if ref in actual_paths:
            valid_refs.append(ref)
        elif ref in REFERENCE_CORRECTIONS:
            corrected = REFERENCE_CORRECTIONS[ref]
            if corrected in actual_paths:
                fixable_refs.append((ref, corrected))
            else:
                invalid_refs.append(f"{ref} (correction '{corrected}' also not found)")
        else:
            # Only try partial match for shorthand refs (not starting with MODULE_)
            if not ref.startswith('MODULE_'):
                # Try to find where this protocol actually exists
                matching = [p for p in actual_paths 
                           if p.endswith('.' + ref) and p.startswith('MODULE_')]
                if matching:
                    fixable_refs.append((ref, matching[0]))
                else:
                    invalid_refs.append(ref)
            else:
                # Full MODULE_ path that doesn't exist
                invalid_refs.append(ref)
    
    return valid_refs, invalid_refs, fixable_refs


def apply_fixes(file_path: str, fixes: List[Tuple[str, str]], dry_run: bool = True) -> str:
    """
    Apply reference fixes to the file.
    
    Args:
        file_path: Path to system_instructions_new.txt
        fixes: List of (old_ref, new_ref) tuples
        dry_run: If True, only report what would be changed
    
    Returns:
        Report of changes made or to be made
    """
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    report_lines = []
    modified_content = content
    
    for old_ref, new_ref in fixes:
        # Count occurrences
        count = content.count(f'"{old_ref}"')
        if count > 0:
            if not dry_run:
                modified_content = modified_content.replace(f'"{old_ref}"', f'"{new_ref}"')
            report_lines.append(f"  '{old_ref}' -> '{new_ref}' ({count} occurrences)")
    
    if not dry_run and modified_content != content:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(modified_content)
        report_lines.insert(0, "APPLIED FIXES:")
    else:
        report_lines.insert(0, "FIXES TO APPLY (dry run):")
    
    return '\n'.join(report_lines)


def generate_report(instructions: dict) -> str:
    """Generate a comprehensive validation report."""
    valid_refs, invalid_refs, fixable_refs = validate_references(instructions)
    
    report = []
    report.append("=" * 60)
    report.append("PROTOCOL REFERENCE VALIDATION REPORT")
    report.append("=" * 60)
    report.append("")
    
    report.append(f"✅ Valid References: {len(valid_refs)}")
    report.append(f"⚠️  Fixable References: {len(fixable_refs)}")
    report.append(f"❌ Invalid References: {len(invalid_refs)}")
    report.append("")
    
    if fixable_refs:
        report.append("-" * 40)
        report.append("FIXABLE REFERENCES (shorthand -> full path):")
        report.append("-" * 40)
        for old, new in fixable_refs:
            report.append(f"  {old}")
            report.append(f"    -> {new}")
        report.append("")
    
    if invalid_refs:
        report.append("-" * 40)
        report.append("INVALID REFERENCES (need manual review):")
        report.append("-" * 40)
        for ref in invalid_refs:
            report.append(f"  ❌ {ref}")
        report.append("")
    
    return '\n'.join(report)


def main():
    """Main entry point for the validator."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Validate protocol references in system instructions')
    parser.add_argument('--fix', action='store_true', help='Apply fixes (otherwise dry run)')
    parser.add_argument('--file', default='app/resources/system_instructions_new.txt', 
                        help='Path to system instructions file')
    args = parser.parse_args()
    
    file_path = Path(__file__).parent.parent / args.file
    if not file_path.exists():
        file_path = Path(args.file)
    
    if not file_path.exists():
        logger.error(f"File not found: {file_path}")
        return 1
    
    logger.info(f"Loading: {file_path}")
    
    try:
        instructions = load_instructions(str(file_path))
    except json.JSONDecodeError as e:
        logger.error(f"JSON parse error: {e}")
        return 1
    
    # Generate and print report
    report = generate_report(instructions)
    print(report)
    
    # Get fixable refs for potential application
    _, _, fixable_refs = validate_references(instructions)
    
    if fixable_refs:
        fix_report = apply_fixes(str(file_path), fixable_refs, dry_run=not args.fix)
        print(fix_report)
        
        if args.fix:
            logger.info("Fixes applied successfully!")
        else:
            logger.info("Run with --fix to apply these changes")
    
    return 0


if __name__ == '__main__':
    exit(main())
