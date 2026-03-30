import argparse
import shutil
import os
import sys


def apply_fix(filepath: str) -> None:
    """
    Apply a one-off patch to the given email_settings.py file by inserting
    a None-check for active_groups_select at the expected location.

    A backup <filepath>.bak is created before modifying the file. If writing
    fails, the backup is restored.
    """
    with open(filepath, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    # Insert None-check after line 758 (0-indexed: 757 is the def line)
    # We need to insert after line 758 (1-indexed), so index 758 in 0-indexed
    lines.insert(759, '                    if active_groups_select is None:\n')
    lines.insert(760, '                        return\n')

    # Create backup before writing
    backup_path = filepath + ".bak"
    try:
        shutil.copy2(filepath, backup_path)
        print(f"Created backup at {backup_path}")
    except Exception as e:
        print(f"Failed to create backup, aborting: {e}")
        sys.exit(1)

    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.writelines(lines)
    except Exception as e:
        print(f"Error writing file: {e}")
        # Restore from backup if write failed partially
        if os.path.exists(backup_path):
            try:
                shutil.copy2(backup_path, filepath)
                print("Restored from backup.")
            except Exception as restore_error:
                print(f"CRITICAL: Failed to restore from backup: {restore_error}")
        sys.exit(1)

    print('Fixed _apply_active_groups with None-check')


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Apply a one-off fix to an email_settings.py file by inserting a "
            "None-check for active_groups_select at the expected location."
        )
    )
    parser.add_argument(
        "filepath",
        help=(
            "Path to the email_settings.py file to patch. "
            "Relative or absolute paths are supported."
        ),
    )
    args = parser.parse_args()
    apply_fix(args.filepath)


if __name__ == "__main__":
    main()
