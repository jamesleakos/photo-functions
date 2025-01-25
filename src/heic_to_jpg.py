#!/usr/bin/env python3

import os
from pathlib import Path
from tqdm import tqdm
from PIL import Image
from pillow_heif import register_heif_opener
import subprocess
import json
from datetime import datetime

def get_heic_metadata(file_path):
    """Get all metadata from HEIC file using exiftool."""
    try:
        result = subprocess.run(
            ['exiftool', '-json', file_path],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            return None
        return json.loads(result.stdout)[0]
    except Exception as e:
        print(f"Error reading metadata: {str(e)}")
        return None

def copy_metadata_to_jpg(heic_path, jpg_path):
    """Copy all metadata from HEIC to JPG using exiftool."""
    try:
        # Copy all metadata
        subprocess.run(
            ['exiftool', '-TagsFromFile', str(heic_path), str(jpg_path)],
            check=True, capture_output=True
        )
        
        # Remove the backup file created by exiftool
        backup_file = Path(str(jpg_path) + '_original')
        if backup_file.exists():
            backup_file.unlink()
            
        return True
    except Exception as e:
        print(f"Error copying metadata: {str(e)}")
        return False

def convert_heic_to_jpg(folder_path, delete_original=False):
    """Convert all HEIC files in a folder to JPG format, preserving metadata."""
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
            # Create output path
            jpg_path = heic_file.with_suffix('.jpg')
            
            # Convert HEIC to JPG
            with Image.open(heic_file) as img:
                # Convert and save as JPG with high quality
                img.convert('RGB').save(jpg_path, 'JPEG', quality=95)
            
            # Copy all metadata from HEIC to JPG
            if copy_metadata_to_jpg(heic_file, jpg_path):
                converted_count += 1
                
                # Delete original if requested and conversion was successful
                if delete_original:
                    os.remove(heic_file)
            else:
                errors.append((heic_file, "Failed to copy metadata"))

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