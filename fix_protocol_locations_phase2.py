import json
import sys

FILE_PATH = '/home/robin/watibot4/app/resources/system_instructions_new.txt'

def move_protocol(data, source_path, target_module, target_key=None):
    # Navigate to source
    parts = source_path.split('.')
    current = data
    parent = None
    last_key = None
    
    for part in parts:
        if isinstance(current, dict) and part in current:
            parent = current
            last_key = part
            current = current[part]
        else:
            print(f"❌ Source not found: {source_path}")
            return False
    
    # Extract
    protocol_data = parent.pop(last_key)
    print(f"✅ Extracted {last_key} from {source_path}")
    
    # Navigate to target module
    if target_module not in data:
        print(f"❌ Target module not found: {target_module}")
        # Put it back? No, just fail for now.
        return False
    
    target_name = target_key if target_key else last_key
    data[target_module][target_name] = protocol_data
    print(f"✅ Moved to {target_module}.{target_name}")
    return True

def fix_phase2():
    print(f"Loading {FILE_PATH}...")
    with open(FILE_PATH, 'r') as f:
        data = json.load(f)

    moves = [
        # Source Path (dot notation starting from root or module), Target Module
        ('MODULE_4_INFORMATION.hotel_information.payment_methods', 'MODULE_2B_PRICE_INQUIRY'),
        ('MODULE_4_INFORMATION.operations_notification_protocol', 'MODULE_3_SERVICE_FLOWS'),
        ('MODULE_4_INFORMATION.hotel_information.special_services_and_add_ons.custom_decoration_request_protocol', 'MODULE_3_SERVICE_FLOWS'),
        ('MODULE_3_SERVICE_FLOWS.check_in_out_direct_query_protocol', 'MODULE_1_CRITICAL_WORKFLOWS'),
        ('MODULE_2A_PACKAGE_CONTENT.package_alias_protocol', 'MODULE_2B_PRICE_INQUIRY'),
        ('MODULE_2C_AVAILABILITY.availability_and_booking_protocol.same_day_booking_protocol', 'MODULE_2D_SPECIAL_SCENARIOS'),
        ('MODULE_2C_AVAILABILITY.booking_time_availability_protocol', 'MODULE_2B_PRICE_INQUIRY')
    ]

    for source, target in moves:
        move_protocol(data, source, target)

    print("Saving changes...")
    with open(FILE_PATH, 'w') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print("Done.")

if __name__ == "__main__":
    fix_phase2()
