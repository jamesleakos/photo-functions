#!/usr/bin/env python3

import os
from pathlib import Path
from tqdm import tqdm
from PIL import Image
from pillow_heif import register_heif_opener

def convert_heic_to_jpg(folder_path, delete_original=False):
    """Convert all HEIC files in a folder to JPG format."""
    # Register HEIF opener with Pillow
    register_heif_opener()

    # Get all HEIC files
    heic_files = []
    for path in Path(folder_path).rglob('*'):
        if path.suffix.lower() in {'.heic'}:
            heic_files.append(path)

    if not heic_files:
        print("No HEIC files found in the specified folder.")
        return

    print(f"Found {len(heic_files)} HEIC files. Converting...")
    converted_count = 0
    errors = []

    for heic_file in tqdm(heic_files):
        try:
            # Open HEIC file
            with Image.open(heic_file) as img:
                # Create output path
                jpg_path = heic_file.with_suffix('.jpg')
                
                # Convert and save as JPG
                img.convert('RGB').save(jpg_path, 'JPEG', quality=95)
                converted_count += 1

                # Delete original if requested
                if delete_original:
                    os.remove(heic_file)

        except Exception as e:
            errors.append((heic_file, str(e)))
            continue

    # Print summary
    print(f"\nConversion complete!")
    print(f"Successfully converted: {converted_count} files")
    if errors:
        print(f"Failed to convert: {len(errors)} files")
        print("\nErrors:")
        for file, error in errors:
            print(f"{file}: {error}")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Convert HEIC files to JPG format")
    parser.add_argument("folder_path", help="Folder containing HEIC files to convert")
    parser.add_argument("--delete-original", action="store_true", 
                      help="Delete original HEIC files after successful conversion")
    args = parser.parse_args()

    convert_heic_to_jpg(args.folder_path, args.delete_original) 