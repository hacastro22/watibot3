#!/usr/bin/env python3
"""
Automated script to split MODULE_2_SALES_FLOWS into 4 sub-modules.
Uses JSON manipulation to ensure syntax correctness.
"""

import json
import sys
from pathlib import Path

def main():
    # Load the current system instructions
    instructions_path = Path("app/resources/system_instructions_new.txt")
    
    print("📖 Loading system_instructions_new.txt...")
    with open(instructions_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    # Backup original file
    backup_path = instructions_path.with_suffix('.txt.backup')
    print(f"💾 Creating backup at {backup_path}...")
    with open(backup_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    # Extract MODULE_2_SALES_FLOWS
    if "MODULE_2_SALES_FLOWS" not in data:
        print("❌ MODULE_2_SALES_FLOWS not found!")
        sys.exit(1)
    
    module_2 = data["MODULE_2_SALES_FLOWS"]
    print(f"✅ Found MODULE_2_SALES_FLOWS with {len(json.dumps(module_2))} characters")
    
    # Create MODULE_2A_PACKAGE_CONTENT
    print("\n🔨 Creating MODULE_2A_PACKAGE_CONTENT...")
    sales_logic = module_2.get("sales_and_booking_logic", {})
    data["MODULE_2A_PACKAGE_CONTENT"] = {
        "description": "Package content descriptions and what's included in each package",
        "package_content_inquiry_protocol": sales_logic.get("package_content_inquiry_protocol", {}),
        "package_alias_protocol": module_2.get("package_alias_protocol", {}),
        "core_sales_rule_packages_and_accommodations": sales_logic.get("core_sales_rule_packages_and_accommodations", {}),
        "escapadita_package_logic": sales_logic.get("escapadita_package_logic", {})
    }
    
    # Create MODULE_2B_PRICE_INQUIRY
    print("🔨 Creating MODULE_2B_PRICE_INQUIRY...")
    data["MODULE_2B_PRICE_INQUIRY"] = {
        "description": "Pricing, quotes, and payment handling for bookings",
        "quote_generation_protocol": module_2.get("quote_generation_protocol", {}),
        "daypass_sales_protocol": module_2.get("daypass_sales_protocol", {}),
        "booking_urgency_protocol": module_2.get("booking_urgency_protocol", {}),
        "multi_option_quote_protocol": module_2.get("multi_option_quote_protocol", {}),
        "proactive_quoting_mandate": sales_logic.get("proactive_quoting_mandate", {}),
        "reservation_vs_walk_in_policy": sales_logic.get("reservation_vs_walk_in_policy", {})
    }
    
    # Create MODULE_2C_AVAILABILITY
    print("🔨 Creating MODULE_2C_AVAILABILITY...")
    data["MODULE_2C_AVAILABILITY"] = {
        "description": "Room availability checks and accommodation selection",
        "availability_tool_selection_protocol": module_2.get("availability_tool_selection_protocol", {}),
        "availability_and_booking_protocol": module_2.get("availability_and_booking_protocol", {}),
        "booking_time_availability_protocol": module_2.get("booking_time_availability_protocol", {}),
        "accommodation_selection_cot_protocol": module_2.get("accommodation_selection_cot_protocol", {})
    }
    
    # Create MODULE_2D_SPECIAL_SCENARIOS
    print("🔨 Creating MODULE_2D_SPECIAL_SCENARIOS...")
    data["MODULE_2D_SPECIAL_SCENARIOS"] = {
        "description": "Special scenarios: membership, all-inclusive objections, special events",
        "membership_sales_protocol": module_2.get("membership_sales_protocol", {}),
        "all_inclusive_inquiry_protocol": module_2.get("all_inclusive_inquiry_protocol", {}),
        "new_year_party_inquiry_protocol": module_2.get("new_year_party_inquiry_protocol", {}),
        "special_date_notification_protocol": module_2.get("special_date_notification_protocol", {})
    }
    
    # Delete MODULE_2_SALES_FLOWS
    print("\n🗑️  Removing MODULE_2_SALES_FLOWS...")
    del data["MODULE_2_SALES_FLOWS"]
    
    # Update MODULE_SYSTEM descriptions
    print("\n📝 Updating MODULE_SYSTEM.on_demand_modules descriptions...")
    if "MODULE_SYSTEM" in data and "on_demand_modules" in data["MODULE_SYSTEM"]:
        on_demand = data["MODULE_SYSTEM"]["on_demand_modules"]
        
        # Remove MODULE_2_SALES_FLOWS
        if "MODULE_2_SALES_FLOWS" in on_demand:
            del on_demand["MODULE_2_SALES_FLOWS"]
            
        # Add new sub-module descriptions
        on_demand["MODULE_2A_PACKAGE_CONTENT"] = {
            "description": "Package descriptions and what's included",
            "size": "~4,200 tokens",
            "when_to_load": "Customer asks 'qué incluye', package details, or comparisons"
        }
        on_demand["MODULE_2B_PRICE_INQUIRY"] = {
            "description": "Pricing, quotes, payment handling",
            "size": "~5,100 tokens",
            "when_to_load": "Customer wants prices, quotes, or payment options",
            "critical_note": "Contains Romántico +$20 surcharge rule"
        }
        on_demand["MODULE_2C_AVAILABILITY"] = {
            "description": "Room availability and inventory checks",
            "size": "~3,200 tokens",
            "when_to_load": "Customer asks about disponibilidad or room types"
        }
        on_demand["MODULE_2D_SPECIAL_SCENARIOS"] = {
            "description": "Membership, all-inclusive objections, special events",
            "size": "~2,200 tokens",
            "when_to_load": "Membership inquiries, all-inclusive questions, New Year's party",
            "micro_load_capable": True
        }
    
    # Update MODULE_DEPENDENCIES references
    print("📝 Updating MODULE_DEPENDENCIES references...")
    
    def update_references(obj, path=""):
        """Recursively update MODULE_2_SALES_FLOWS references"""
        if isinstance(obj, dict):
            for key, value in list(obj.items()):
                if isinstance(value, str):
                    # Update string references
                    if "MODULE_2_SALES_FLOWS" in value:
                        if "payment_verification" in value or "booking_completion" in value:
                            obj[key] = value.replace("MODULE_2_SALES_FLOWS", "MODULE_2B_PRICE_INQUIRY")
                        elif "quote_generation" in value:
                            obj[key] = value.replace("MODULE_2_SALES_FLOWS", "MODULE_2B_PRICE_INQUIRY")
                        elif "package_content_inquiry_protocol" in value:
                            obj[key] = value.replace("MODULE_2_SALES_FLOWS", "MODULE_2A_PACKAGE_CONTENT")
                        elif "all_inclusive_inquiry_protocol" in value:
                            obj[key] = value.replace("MODULE_2_SALES_FLOWS", "MODULE_2D_SPECIAL_SCENARIOS")
                        elif "availability_tool_selection_protocol" in value:
                            obj[key] = value.replace("MODULE_2_SALES_FLOWS", "MODULE_2C_AVAILABILITY")
                        elif "membership_sales_protocol" in value:
                            obj[key] = value.replace("MODULE_2_SALES_FLOWS", "MODULE_2D_SPECIAL_SCENARIOS")
                        else:
                            # Generic replacement for blocking rules
                            obj[key] = value.replace(
                                "MODULE_2_SALES_FLOWS",
                                "MODULE_2A_PACKAGE_CONTENT, MODULE_2B_PRICE_INQUIRY, MODULE_2C_AVAILABILITY, MODULE_2D_SPECIAL_SCENARIOS"
                            )
                elif isinstance(value, list):
                    # Update list items
                    for i, item in enumerate(value):
                        if isinstance(item, str) and "MODULE_2_SALES_FLOWS" in item:
                            # Determine which sub-module based on context
                            if "payment" in item.lower() or "booking" in item.lower():
                                value[i] = item.replace("MODULE_2_SALES_FLOWS", "MODULE_2B_PRICE_INQUIRY")
                            elif "package" in item.lower() and "content" in item.lower():
                                value[i] = item.replace("MODULE_2_SALES_FLOWS", "MODULE_2A_PACKAGE_CONTENT")
                            elif "availability" in item.lower():
                                value[i] = item.replace("MODULE_2_SALES_FLOWS", "MODULE_2C_AVAILABILITY")
                            elif "membership" in item.lower() or "all_inclusive" in item.lower():
                                value[i] = item.replace("MODULE_2_SALES_FLOWS", "MODULE_2D_SPECIAL_SCENARIOS")
                            else:
                                value[i] = item.replace("MODULE_2_SALES_FLOWS", "MODULE_2B_PRICE_INQUIRY")
                        elif isinstance(item, (dict, list)):
                            update_references(item, f"{path}.{key}[{i}]")
                else:
                    update_references(value, f"{path}.{key}")
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                update_references(item, f"{path}[{i}]")
    
    if "MODULE_DEPENDENCIES" in data:
        update_references(data["MODULE_DEPENDENCIES"])
    
    if "DECISION_TREE" in data:
        update_references(data["DECISION_TREE"])
    
    # Write updated file
    print("\n💾 Writing updated system_instructions_new.txt...")
    with open(instructions_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    
    # Validation
    print("\n✅ Validating JSON structure...")
    with open(instructions_path, 'r', encoding='utf-8') as f:
        json.load(f)  # Will raise exception if invalid
    
    print("\n🎉 SUCCESS! Module restructuring complete.")
    print(f"   - Created: MODULE_2A_PACKAGE_CONTENT")
    print(f"   - Created: MODULE_2B_PRICE_INQUIRY")
    print(f"   - Created: MODULE_2C_AVAILABILITY")
    print(f"   - Created: MODULE_2D_SPECIAL_SCENARIOS")
    print(f"   - Removed: MODULE_2_SALES_FLOWS")
    print(f"   - Updated: All references in MODULE_DEPENDENCIES and DECISION_TREE")
    print(f"\n📋 Backup saved at: {backup_path}")
    print(f"\n⚠️  Next steps:")
    print(f"   1. Review the changes in system_instructions_new.txt")
    print(f"   2. Test with sample queries")
    print(f"   3. If issues occur, restore from backup")

if __name__ == "__main__":
    main()
