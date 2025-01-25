#!/usr/bin/env python3

import os
from pathlib import Path
from tqdm import tqdm
from PIL import Image, ImageOps
from pillow_heif import register_heif_opener, HeifImagePlugin
import subprocess
import json
from datetime import datetime

def get_heic_metadata(file_path):
    """Get all metadata from HEIC file using exiftool."""
    try:
        result = subprocess.run(
            ['exiftool', '-json', '-a', '-b', file_path],
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
        # Copy all metadata except orientation since we've already applied it
        # Use -a flag to preserve all edits and adjustments
        subprocess.run(
            ['exiftool', '-TagsFromFile', str(heic_path),
             '-all:all', '-orientation#=1', '-overwrite_original',
             '-a', str(jpg_path)],
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

def has_edits(heic_path):
    """Check if the HEIC file has been edited."""
    try:
        result = subprocess.run(
            ['exiftool', '-json', '-AdjustmentType', '-HasCrop', str(heic_path)],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)[0]
            return 'AdjustmentType' in data or 'HasCrop' in data
        return False
    except:
        return False

def convert_heic_to_jpg(folder_path, delete_original=False):
    """Convert all HEIC files in a folder to JPG format, preserving metadata, orientation, and edits."""
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
    edited_count = 0
    original_count = 0
    errors = []

    for heic_file in tqdm(heic_files):
        try:
            # Create output path
            jpg_path = heic_file.with_suffix('.jpg')
            
            # Check if file has edits
            has_iphone_edits = has_edits(heic_file)
            
            # First, try to extract the edited version if it exists
            if has_iphone_edits:
                print(f"\nProcessing edited photo: {heic_file.name}")
                subprocess.run(
                    ['exiftool', '-b', '-PreviewImage', str(heic_file)],
                    stdout=open(jpg_path, 'wb'),
                    check=False  # Don't raise error if no preview exists
                )
            
            # If no preview image or no edits, convert normally
            if not os.path.exists(jpg_path) or os.path.getsize(jpg_path) == 0:
                if has_iphone_edits:
                    print(f"No preview image found for edited photo: {heic_file.name}, converting original")
                else:
                    print(f"\nProcessing original photo: {heic_file.name}")
                with Image.open(heic_file) as img:
                    # Apply orientation based on EXIF
                    img = ImageOps.exif_transpose(img)
                    
                    # Convert and save as JPG with high quality
                    img.convert('RGB').save(jpg_path, 'JPEG', quality=95)
                original_count += 1
            else:
                edited_count += 1
            
            # Copy all metadata from HEIC to JPG, but set orientation to normal
            # since we've already applied the rotation
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
    print(f"Total converted: {converted_count} files")
    print(f"  - With iPhone edits: {edited_count}")
    print(f"  - Original photos: {original_count}")
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