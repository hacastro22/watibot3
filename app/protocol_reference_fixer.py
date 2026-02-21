"""
Protocol Reference Fixer for system_instructions_new.txt

This tool fixes protocol references after the file was restructured with shorter names.
The references in DECISION_TREE and MODULE_DEPENDENCIES need to be updated to match
the new protocol names in the actual modules.

Run: python3 -m app.protocol_reference_fixer [--fix]
"""

import json
import re
import logging
from pathlib import Path
from typing import Dict, List, Tuple, Set

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)


# Complete mapping of OLD references → NEW references based on the restructured file
REFERENCE_MAPPINGS = {
    # MODULE_1_CRITICAL_WORKFLOWS - protocol name changes
    "MODULE_1_CRITICAL_WORKFLOWS.cancellation_and_no_show_protocol": "MODULE_1_CRITICAL_WORKFLOWS.cancellation_no_show",
    "MODULE_1_CRITICAL_WORKFLOWS.check_in_out_direct_query_protocol": "MODULE_1_CRITICAL_WORKFLOWS.check_in_out_query",
    "MODULE_1_CRITICAL_WORKFLOWS.check_in_out_direct_query_protocol.exact_response_script": "MODULE_1_CRITICAL_WORKFLOWS.check_in_out_query.script",
    "MODULE_1_CRITICAL_WORKFLOWS.friendly_goodbye_protocol": "MODULE_1_CRITICAL_WORKFLOWS.friendly_goodbye",
    "MODULE_1_CRITICAL_WORKFLOWS.group_and_event_handling_protocol": "MODULE_1_CRITICAL_WORKFLOWS.group_event_handling",
    "MODULE_1_CRITICAL_WORKFLOWS.handover_protocol": "MODULE_1_CRITICAL_WORKFLOWS.handover",
    "MODULE_1_CRITICAL_WORKFLOWS.incomplete_booking_completion_protocol": "MODULE_1_CRITICAL_WORKFLOWS.incomplete_booking_completion",
    "MODULE_1_CRITICAL_WORKFLOWS.language_switching_protocol": "MODULE_1_CRITICAL_WORKFLOWS.language_switching",
    "MODULE_1_CRITICAL_WORKFLOWS.member_handling_protocol": "MODULE_1_CRITICAL_WORKFLOWS.member_handling",
    "MODULE_1_CRITICAL_WORKFLOWS.member_handling_protocol.initial_redirection_script": "MODULE_1_CRITICAL_WORKFLOWS.member_handling.initial_redirection_script",
    
    # MODULE_2A_PACKAGE_CONTENT - protocol name changes
    "MODULE_2A_PACKAGE_CONTENT.package_content_inquiry_protocol": "MODULE_2A_PACKAGE_CONTENT.package_details",
    "MODULE_2A_PACKAGE_CONTENT.package_inquiry": "MODULE_2A_PACKAGE_CONTENT.package_details",
    
    # MODULE_2B_PRICE_INQUIRY - nested path changes
    "MODULE_2B_PRICE_INQUIRY.daypass_sales_protocol.weekend_inquiry_protocol": "MODULE_2B_PRICE_INQUIRY.daypass_sales_protocol.weekend_inquiry",
    
    # MODULE_2C_AVAILABILITY - protocol name changes
    "MODULE_2C_AVAILABILITY.availability_tool_selection_protocol": "MODULE_2C_AVAILABILITY.tool_selection",
    "MODULE_2C_AVAILABILITY.booking_temporal_logic": "MODULE_2C_AVAILABILITY.booking_temporal",
    "MODULE_2C_AVAILABILITY.date_validation_and_disambiguation_protocol": "MODULE_2C_AVAILABILITY.date_validation",
    "MODULE_2C_AVAILABILITY.occupancy_enforcement_protocol": "MODULE_2C_AVAILABILITY.occupancy_rules",
    
    # MODULE_4_INFORMATION - major restructuring
    "MODULE_4_INFORMATION.accommodation_suggestion_logic": "MODULE_4_INFORMATION.accommodations.priority",
    "MODULE_4_INFORMATION.accommodations.bungalow_types": "MODULE_4_INFORMATION.accommodations.types",
    "MODULE_4_INFORMATION.accommodations.occupancy_rules": "MODULE_4_INFORMATION.accommodations.occupancy_calc",
    "MODULE_4_INFORMATION.baby_food_exception_protocol": "MODULE_4_INFORMATION.baby_food_exception",
    "MODULE_4_INFORMATION.day_use_room_policy": "MODULE_4_INFORMATION.day_use_room",
    "MODULE_4_INFORMATION.facilities_and_amenities": "MODULE_4_INFORMATION.facilities",
    "MODULE_4_INFORMATION.general_policies": "MODULE_4_INFORMATION.policies_basic",
    "MODULE_4_INFORMATION.general_policies.cooking_policy": "MODULE_4_INFORMATION.policies_basic.cooking",
    "MODULE_4_INFORMATION.hotel_capacity_query_protocol": "MODULE_4_INFORMATION.capacity_query",
    # hotel_information was split into hotel_location_access, hotel_checkin_policies, hotel_restaurant_menu, hotel_rooms_facilities
    "MODULE_4_INFORMATION.hotel_information": "MODULE_4_INFORMATION.hotel_location_access",
    "MODULE_4_INFORMATION.hotel_information.check_in_out_policy": "MODULE_4_INFORMATION.hotel_checkin_policies.check_in_out",
    "MODULE_4_INFORMATION.hotel_information.check_in_out": "MODULE_4_INFORMATION.hotel_checkin_policies.check_in_out",
    "MODULE_4_INFORMATION.hotel_information.contact_info": "MODULE_4_INFORMATION.hotel_location_access.contact",
    "MODULE_4_INFORMATION.hotel_information.contact": "MODULE_4_INFORMATION.hotel_location_access.contact",
    "MODULE_4_INFORMATION.hotel_information.facilities": "MODULE_4_INFORMATION.facilities",
    "MODULE_4_INFORMATION.hotel_information.general_regulations.pet_policy": "MODULE_4_INFORMATION.hotel_checkin_policies.regulations.pets",
    "MODULE_4_INFORMATION.hotel_information.regulations": "MODULE_4_INFORMATION.hotel_checkin_policies.regulations",
    "MODULE_4_INFORMATION.hotel_information.location": "MODULE_4_INFORMATION.hotel_location_access.location",
    "MODULE_4_INFORMATION.hotel_information.location.access_route_protocol": "MODULE_4_INFORMATION.hotel_location_access.location.access_route_script",
    "MODULE_4_INFORMATION.hotel_information.location.access_route_script": "MODULE_4_INFORMATION.hotel_location_access.location.access_route_script",
    "MODULE_4_INFORMATION.hotel_information.menu_information_protocol": "MODULE_4_INFORMATION.hotel_restaurant_menu.menu_protocol",
    "MODULE_4_INFORMATION.hotel_information.menu_protocol": "MODULE_4_INFORMATION.hotel_restaurant_menu.menu_protocol",
    "MODULE_4_INFORMATION.hotel_information.pool_rules": "MODULE_4_INFORMATION.hotel_rooms_facilities.pool",
    "MODULE_4_INFORMATION.hotel_information.pool": "MODULE_4_INFORMATION.hotel_rooms_facilities.pool",
    "MODULE_4_INFORMATION.hotel_information.restaurant_hours": "MODULE_4_INFORMATION.hotel_restaurant_menu.restaurant.hours",
    "MODULE_4_INFORMATION.hotel_information.restaurant": "MODULE_4_INFORMATION.hotel_restaurant_menu.restaurant",
    "MODULE_4_INFORMATION.hotel_information.towel_policy": "MODULE_4_INFORMATION.hotel_checkin_policies.towels",
    "MODULE_4_INFORMATION.hotel_information.towels": "MODULE_4_INFORMATION.hotel_checkin_policies.towels",
    "MODULE_4_INFORMATION.hotel_information.weekend_entertainment_schedule": "MODULE_4_INFORMATION.hotel_rooms_facilities.weekend_entertainment",
    "MODULE_4_INFORMATION.hotel_information.weekend_entertainment": "MODULE_4_INFORMATION.hotel_rooms_facilities.weekend_entertainment",
    "MODULE_4_INFORMATION.hotel_information.wifi_policy": "MODULE_4_INFORMATION.hotel_rooms_facilities.wifi",
    "MODULE_4_INFORMATION.hotel_information.wifi": "MODULE_4_INFORMATION.hotel_rooms_facilities.wifi",
    "MODULE_4_INFORMATION.invitational_event_query_protocol": "MODULE_4_INFORMATION.invitational_event",
    "MODULE_4_INFORMATION.job_inquiry_protocol": "MODULE_4_INFORMATION.job_inquiry",
    "MODULE_4_INFORMATION.lost_and_found_protocol": "MODULE_4_INFORMATION.lost_and_found",
    "MODULE_4_INFORMATION.parking_information_protocol": "MODULE_4_INFORMATION.parking",
    "MODULE_4_INFORMATION.transportation_request_protocol": "MODULE_4_INFORMATION.transportation",
    
    # Additional shorthand fixes from previous session
    "accommodations.bungalow_occupancy": "MODULE_4_INFORMATION.accommodations.occupancy_calc",
    "accommodations.room_details": "MODULE_4_INFORMATION.accommodations.types",
    "accommodation_suggestion_logic": "MODULE_4_INFORMATION.accommodations.priority",
    # payment_methods was split into payment_methods_overview and credit_card_payment_protocol
    "payment_methods": "MODULE_2B_PRICE_INQUIRY.payment_methods_overview",
    "MODULE_2B_PRICE_INQUIRY.payment_methods": "MODULE_2B_PRICE_INQUIRY.payment_methods_overview",
    "MODULE_2B_PRICE_INQUIRY.payment_methods.bank_deposit_info": "MODULE_2B_PRICE_INQUIRY.payment_methods_overview.bank_deposit_info",
    "MODULE_2B_PRICE_INQUIRY.payment_methods.credit_card_payment_protocol": "MODULE_2B_PRICE_INQUIRY.credit_card_payment_protocol",
    "payment_objection_handling_protocol": "MODULE_2B_PRICE_INQUIRY.payment_objection_handling_protocol",
    
    # CORE_CONFIG shorthand references (MODULE_1 → MODULE_1_CRITICAL_WORKFLOWS)
    "MODULE_1.member_handling_protocol": "MODULE_1_CRITICAL_WORKFLOWS.member_handling",
    "MODULE_1.member_handling_protocol.initial_redirection_script": "MODULE_1_CRITICAL_WORKFLOWS.member_handling.initial_redirection_script",
    
    # MODULE_2D references
    # holiday_activities_protocol was split into holiday_activities_rules and holiday_resort_schedule
    "MODULE_2D.holiday_activities_protocol": "MODULE_2D_SPECIAL_SCENARIOS.holiday_activities_rules",
    "MODULE_2D_SPECIAL_SCENARIOS.holiday_activities_protocol": "MODULE_2D_SPECIAL_SCENARIOS.holiday_activities_rules",
    # multi_room_booking_protocol was split into multi_room_booking_rules and multi_room_booking_examples
    "MODULE_2B_PRICE_INQUIRY.multi_room_booking_protocol": "MODULE_2B_PRICE_INQUIRY.multi_room_booking_rules",
    # promotion_rules was split into pasadia_5x4_promotion and promotion_inquiry_protocol
    "MODULE_4_INFORMATION.promotion_rules": "MODULE_4_INFORMATION.promotion_inquiry_protocol",
    
    # Shorthand module prefixes (MODULE_2B → MODULE_2B_PRICE_INQUIRY, MODULE_4 → MODULE_4_INFORMATION)
    "MODULE_2B.bank_deposit_info": "MODULE_2B_PRICE_INQUIRY.payment_methods_overview.bank_deposit_info",
    "MODULE_4.hotel_information.location": "MODULE_4_INFORMATION.hotel_location_access.location",
}


def load_instructions(file_path: str) -> dict:
    """Load and parse the system instructions JSON file."""
    with open(file_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def get_all_actual_paths(obj: dict, current_path: str = "") -> Set[str]:
    """
    Recursively build a set of all actual paths in the instructions.
    """
    paths = set()
    
    if isinstance(obj, dict):
        for key, value in obj.items():
            new_path = f"{current_path}.{key}" if current_path else key
            paths.add(new_path)
            paths.update(get_all_actual_paths(value, new_path))
    
    return paths


def find_all_references(content: str) -> List[Tuple[str, int]]:
    """
    Find all MODULE_ references in the file content.
    Returns list of (reference, line_number) tuples.
    """
    references = []
    lines = content.split('\n')
    
    for line_num, line in enumerate(lines, 1):
        # Find MODULE_X references
        matches = re.findall(r'MODULE_[A-Z0-9_]+(?:\.[a-zA-Z_][a-zA-Z0-9_]*)+', line)
        for match in matches:
            references.append((match, line_num))
    
    return references


def analyze_references(file_path: str) -> Tuple[List[str], List[Tuple[str, str]], List[str]]:
    """
    Analyze all references and categorize them.
    
    Returns:
        - valid_refs: References that exist in the file
        - fixable_refs: (old_ref, new_ref) tuples that can be auto-fixed
        - invalid_refs: References that don't exist and have no known fix
    """
    instructions = load_instructions(file_path)
    actual_paths = get_all_actual_paths(instructions)
    
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    all_refs = find_all_references(content)
    unique_refs = set(ref for ref, _ in all_refs)
    
    valid_refs = []
    fixable_refs = []
    invalid_refs = []
    
    for ref in sorted(unique_refs):
        if ref in actual_paths:
            valid_refs.append(ref)
        elif ref in REFERENCE_MAPPINGS:
            new_ref = REFERENCE_MAPPINGS[ref]
            # Verify the new reference exists
            if new_ref in actual_paths:
                fixable_refs.append((ref, new_ref))
            else:
                # The mapping target doesn't exist - might need updating
                invalid_refs.append(f"{ref} → mapping target '{new_ref}' not found")
        else:
            invalid_refs.append(ref)
    
    return valid_refs, fixable_refs, invalid_refs


def apply_fixes(file_path: str, fixes: List[Tuple[str, str]], dry_run: bool = True) -> str:
    """
    Apply reference fixes to the file.
    
    Handles both:
    1. Standalone references in quotes: "MODULE_X.path"
    2. Embedded references in text: "... → MODULE_X.path ..."
    
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
    total_changes = 0
    
    # Sort by length (longest first) to avoid partial matches
    for old_ref, new_ref in sorted(fixes, key=lambda x: -len(x[0])):
        # Use regex to find exact occurrences (not partial matches)
        # Match the reference when surrounded by non-alphanumeric chars (or start/end)
        pattern = re.compile(
            r'(?<![a-zA-Z0-9_])' + re.escape(old_ref) + r'(?![a-zA-Z0-9_])'
        )
        
        matches = pattern.findall(modified_content)
        count = len(matches)
        
        if count > 0:
            if not dry_run:
                modified_content = pattern.sub(new_ref, modified_content)
            report_lines.append(f"  ✓ '{old_ref}'")
            report_lines.append(f"    → '{new_ref}' ({count} occurrence{'s' if count > 1 else ''})")
            total_changes += count
    
    if total_changes == 0:
        return "No fixes needed - all references are valid!"
    
    if not dry_run and modified_content != content:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(modified_content)
        report_lines.insert(0, f"✅ APPLIED {total_changes} FIXES:")
    else:
        report_lines.insert(0, f"🔧 FIXES TO APPLY ({total_changes} changes) - run with --fix:")
    
    return '\n'.join(report_lines)


def generate_report(file_path: str) -> str:
    """Generate a comprehensive validation report."""
    valid_refs, fixable_refs, invalid_refs = analyze_references(file_path)
    
    report = []
    report.append("=" * 60)
    report.append("PROTOCOL REFERENCE ANALYSIS REPORT")
    report.append("=" * 60)
    report.append("")
    
    report.append(f"✅ Valid References: {len(valid_refs)}")
    report.append(f"🔧 Fixable References: {len(fixable_refs)}")
    report.append(f"❌ Invalid/Unknown References: {len(invalid_refs)}")
    report.append("")
    
    if fixable_refs:
        report.append("-" * 60)
        report.append("FIXABLE REFERENCES (old → new):")
        report.append("-" * 60)
        for old, new in fixable_refs:
            report.append(f"  {old}")
            report.append(f"    → {new}")
        report.append("")
    
    if invalid_refs:
        report.append("-" * 60)
        report.append("INVALID/UNKNOWN REFERENCES (need manual review):")
        report.append("-" * 60)
        for ref in invalid_refs:
            report.append(f"  ❌ {ref}")
        report.append("")
        report.append("NOTE: These references may need to be added to REFERENCE_MAPPINGS")
        report.append("      or the target protocols may need to be created/moved.")
        report.append("")
    
    return '\n'.join(report)


def verify_json(file_path: str) -> bool:
    """Verify the file is valid JSON."""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            json.load(f)
        return True
    except json.JSONDecodeError as e:
        logger.error(f"JSON validation failed: {e}")
        return False


def main():
    """Main entry point for the fixer."""
    import argparse
    
    parser = argparse.ArgumentParser(description='Fix protocol references in system instructions')
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
    
    logger.info(f"Analyzing: {file_path}")
    
    # Verify JSON first
    if not verify_json(str(file_path)):
        logger.error("File is not valid JSON. Please fix JSON errors first.")
        return 1
    
    # Generate and print report
    report = generate_report(str(file_path))
    print(report)
    
    # Get fixable refs
    _, fixable_refs, _ = analyze_references(str(file_path))
    
    if fixable_refs:
        fix_report = apply_fixes(str(file_path), fixable_refs, dry_run=not args.fix)
        print(fix_report)
        
        if args.fix:
            # Verify JSON is still valid after fixes
            if verify_json(str(file_path)):
                logger.info("✅ Fixes applied successfully! JSON remains valid.")
            else:
                logger.error("⚠️ Fixes applied but JSON is now invalid! Please review.")
                return 1
        else:
            print("")
            logger.info("Run with --fix to apply these changes")
    
    return 0


if __name__ == '__main__':
    exit(main())
